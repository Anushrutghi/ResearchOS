import base64
from io import BytesIO
import json
import shutil
import tempfile
import unittest
from pathlib import Path
import importlib.util
from unittest.mock import patch

from researchos.workspace import (
    append_chat_message, create_chat, edit_chat_message, get_chat, list_chats,
    list_documents, store_pdf, workspace_reply,
)
from researchos.knowledge.store import add_claim, load_graph, review_relationship, search_knowledge, update_claim_review


class WorkspaceTests(unittest.TestCase):
    def test_chat_history_is_persistent_and_messages_can_be_edited(self):
        with tempfile.TemporaryDirectory() as directory:
            chat = create_chat(directory)
            original = append_chat_message(directory, chat["id"], "user", "First question")
            append_chat_message(directory, chat["id"], "assistant", "Local guidance")
            revised = edit_chat_message(directory, chat["id"], original["id"], "Updated question", "detailed", "note")
            saved = get_chat(directory, chat["id"])
            self.assertEqual([item["content"] for item in saved["messages"]], ["Updated question", revised["assistant_message"]["content"]])
            self.assertEqual(saved["title"], "Updated question")
            self.assertEqual(saved["output_style"], "detailed")
            self.assertEqual(list_chats(directory)[0]["id"], chat["id"])

    def test_pdf_upload_preserves_original_hash_and_provenance(self):
        from pypdf import PdfWriter
        writer = PdfWriter(); writer.add_blank_page(width=200, height=200)
        fixture = BytesIO(); writer.write(fixture)
        with tempfile.TemporaryDirectory() as directory:
            content = fixture.getvalue()
            metadata = store_pdf(directory, "../paper.pdf", base64.b64encode(content).decode())
            self.assertEqual(metadata["filename"], "paper.pdf")
            self.assertEqual(metadata["origin"], "researcher_upload")
            self.assertEqual(len(metadata["sha256"]), 64)
            self.assertTrue((Path(directory) / "documents" / f"{metadata['sha256']}.pdf").exists())
            self.assertEqual(metadata["extraction_status"], "scanned_or_empty")
            self.assertEqual(list_documents(directory)[0]["document_id"], metadata["document_id"])

    def test_pdf_upload_rejects_non_pdf_data_and_extensions(self):
        encoded = base64.b64encode(b"not a pdf").decode()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                store_pdf(directory, "notes.txt", encoded)
            with self.assertRaises(ValueError):
                store_pdf(directory, "paper.pdf", encoded)

    def test_workspace_reply_does_not_claim_to_be_ai_analysis(self):
        response = workspace_reply("What is the current status?")
        self.assertIn("v1.0", response)
        self.assertIn("complete", response)
        self.assertIn("AI provider", response)
        self.assertIn("configured", response)
        concise = workspace_reply("status update", "concise")
        detailed = workspace_reply("status update", "detailed")
        self.assertLess(len(concise), len(detailed))

    def test_reimport_of_identical_pdf_preserves_both_import_events(self):
        from pypdf import PdfWriter
        writer = PdfWriter(); writer.add_blank_page(width=200, height=200)
        fixture = BytesIO(); writer.write(fixture); encoded = base64.b64encode(fixture.getvalue()).decode()
        with tempfile.TemporaryDirectory() as directory:
            first = store_pdf(directory, "first-name.pdf", encoded)
            second = store_pdf(directory, "second-name.pdf", encoded)
        self.assertEqual(first["document_id"], second["document_id"])
        self.assertEqual(second["filename"], "first-name.pdf")
        self.assertEqual(second["filenames"], ["first-name.pdf", "second-name.pdf"])
        self.assertEqual(len(second["imports"]), 2)

    def test_claims_remain_provisional_and_relationships_require_review(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        from researchos.knowledge.store import add_relationship

        writer = PdfWriter(); page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject(); stream.set_data(b"BT /F1 12 Tf 72 720 Td (The treatment improved the measured outcome.) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        fixture = BytesIO(); writer.write(fixture)
        with tempfile.TemporaryDirectory() as directory:
            document = store_pdf(directory, "study.pdf", base64.b64encode(fixture.getvalue()).decode())
            first = add_claim(directory, document["document_id"], 1, "The treatment improved the measured outcome.", "Treatment improved the outcome.")
            second = add_claim(directory, document["document_id"], 1, "The treatment improved the measured outcome.", "Treatment did not improve the outcome.")
            with self.assertRaisesRegex(ValueError, "requires a context note"):
                add_relationship(directory, first["claim_id"], second["claim_id"], "contradicts", " ")
            edge = add_relationship(directory, first["claim_id"], second["claim_id"], "contradicts", "Compare study design before judging this conflict")
            self.assertEqual(edge["status"], "proposed")
            with self.assertRaisesRegex(ValueError, "requires a context note"):
                review_relationship(directory, edge["edge_id"], "rejected", " ")
            rejected = review_relationship(directory, edge["edge_id"], "rejected", "Different outcome definitions")
            self.assertEqual(rejected["status"], "rejected")
            self.assertIn("Different outcome definitions", rejected["reviewer_note"])
            self.assertEqual(load_graph(directory)["claims"][0]["verification_status"], "provisional")

    @unittest.skipUnless(importlib.util.find_spec("pypdfium2") and importlib.util.find_spec("PIL"), "OCR rendering extras are optional")
    def test_scanned_pdf_ocr_keeps_page_number_and_method(self):
        from PIL import Image
        from researchos.knowledge.documents.pdf import extract_pdf
        from subprocess import CompletedProcess

        image = Image.new("RGB", (500, 300), "white")
        fixture = BytesIO(); image.save(fixture, format="PDF", resolution=150)
        with patch("researchos.knowledge.documents.pdf.shutil.which", return_value="/usr/local/bin/tesseract"), \
             patch("researchos.knowledge.documents.pdf.subprocess.run", return_value=CompletedProcess([], 0, b"Scanned source passage", b"")):
            extracted = extract_pdf(fixture.getvalue())
        self.assertEqual(extracted["ocr_status"], "ocr_extracted")
        self.assertEqual(extracted["pages"][0]["page"], 1)
        self.assertIn("Scanned source passage", extracted["pages"][0]["text"])

    @unittest.skipUnless(importlib.util.find_spec("pypdfium2") and importlib.util.find_spec("PIL") and shutil.which("tesseract"), "OCR extras and Tesseract are optional")
    def test_real_tesseract_ocr_smoke(self):
        from PIL import Image, ImageDraw, ImageFont
        from researchos.knowledge.documents.pdf import extract_pdf

        image = Image.new("RGB", (1500, 500), "white")
        ImageDraw.Draw(image).text((60, 160), "Evidence source passage", fill="black", font=ImageFont.load_default(size=72))
        fixture = BytesIO(); image.save(fixture, format="PDF", resolution=150)
        extracted = extract_pdf(fixture.getvalue())
        self.assertEqual(extracted["ocr_status"], "ocr_extracted")
        self.assertIn("evidence", extracted["pages"][0]["text"].lower())

    def test_pdf_page_text_provenance_and_claim_review_are_persisted(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 72 720 Td (Abstract) Tj 0 -18 Td (Evidence supports the measured outcome.) Tj 0 -18 Td (References) Tj 0 -18 Td ([1] Example citation 2024 DOI 10.1234/example) Tj 0 -18 Td (Name   Group   Score) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        writer.add_metadata({"/Title": "Evidence Study", "/Author": "Ada Author"})
        pdf = BytesIO(); writer.write(pdf)

        with tempfile.TemporaryDirectory() as directory:
            document = store_pdf(directory, "study.pdf", base64.b64encode(pdf.getvalue()).decode())
            self.assertEqual(document["extraction_status"], "text_extracted")
            self.assertEqual(document["title"], "Evidence Study")
            self.assertIn("measured outcome", document["pages"][0]["text"])
            self.assertEqual(document["references_status"], "reference_lines_extracted")
            self.assertIn("Example citation", document["references"][0]["text"])
            self.assertEqual(document["references"][0]["year_candidate"], 2024)
            self.assertEqual(document["references"][0]["doi_candidate"], "10.1234/example")
            self.assertEqual(document["tables_status"], "layout_candidates_detected")
            self.assertEqual(document["tables"][0]["rows"][0], ["Name", "Group", "Score"])
            self.assertEqual(document["tables"][0]["status"], "heuristic_candidate")
            claim = add_claim(directory, document["document_id"], 1, "Evidence supports the measured outcome.",
                              "The study reports the measured outcome.", "abstract", "literature_supported_finding")
            self.assertEqual(claim["verification_status"], "provisional")
            self.assertEqual(search_knowledge(directory, "measured outcome")[0]["page"], 1)
            reviewed = update_claim_review(directory, claim["claim_id"], "reviewed", "Checked against page one")
            self.assertEqual(reviewed["verification_status"], "reviewed")
            graph = load_graph(directory)
            self.assertEqual(len(graph["claims"]), 1)
            self.assertTrue(any(node["node_type"] == "paper" and node["properties"].get("doi_candidate") == "10.1234/example" for node in graph["nodes"]))
            self.assertTrue(any(node["node_type"] == "author" and node["label"] == "Ada Author" for node in graph["nodes"]))
            self.assertTrue(any(edge["relationship"] == "authored_by" for edge in graph["edges"]))
            self.assertTrue(any(edge["relationship"] == "derived_from" and edge["status"] == "reviewed" for edge in graph["edges"]))


if __name__ == "__main__":
    unittest.main()

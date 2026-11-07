import base64
from io import BytesIO
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

from pypdf import PdfReader, PdfWriter

from researchos.knowledge.store import add_claim, update_claim_review
from researchos.writing.store import (create_draft, edit_draft, export_draft, get_draft,
    get_export_path, review_draft, writing_catalog)
from researchos.workspace import store_pdf


class WritingTests(unittest.TestCase):
    def make_source(self, directory):
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject(); stream.set_data(b"BT /F1 12 Tf 72 720 Td (Evidence supports the stated outcome.) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        fixture = BytesIO(); writer.write(fixture)
        document = store_pdf(directory, "source.pdf", base64.b64encode(fixture.getvalue()).decode())
        reviewed = add_claim(directory, document["document_id"], 1, "Evidence supports the stated outcome.", "The study reports the stated outcome.")
        provisional = add_claim(directory, document["document_id"], 1, "Evidence supports the stated outcome.", "This claim remains provisional.")
        update_claim_review(directory, reviewed["claim_id"], "reviewed", "Checked on page one")
        return document, reviewed, provisional

    def test_draft_uses_selected_records_and_flags_unreviewed_citations_and_missing_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            document, reviewed, provisional = self.make_source(directory)
            draft = create_draft(directory, {"title": "Outcome study", "document_type": "article",
                "research_question": "What outcome was reported?", "claim_ids": [reviewed["claim_id"], provisional["claim_id"]]})
            self.assertEqual(draft["status"], "draft")
            self.assertIn("[C001]", draft["content"])
            self.assertIn("[NEEDS REVIEW: C002]", draft["content"])
            self.assertIn(document["sha256"], draft["content"])
            self.assertIn("no results have been invented", draft["content"].lower())
            self.assertIn("Results", draft["missing_sections"])
            self.assertEqual(writing_catalog(directory)["claims"][0]["verification_status"], "reviewed")

    def test_export_requires_current_researcher_approval_and_edits_invalidate_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            _, reviewed, _ = self.make_source(directory)
            draft = create_draft(directory, {"title": "Export test", "document_type": "report", "claim_ids": [reviewed["claim_id"]]})
            with self.assertRaisesRegex(ValueError, "must approve"):
                export_draft(directory, draft["draft_id"], "markdown")
            revised = edit_draft(directory, draft["draft_id"], draft["content"] + "\nResearcher note.\n")
            self.assertEqual(revised["status"], "draft")
            with self.assertRaisesRegex(ValueError, "must approve"):
                export_draft(directory, draft["draft_id"], "markdown")
            approved = review_draft(directory, draft["draft_id"], "approve", "Reviewed current text and citations")
            self.assertEqual(approved["status"], "approved")
            item = export_draft(directory, draft["draft_id"], "markdown")
            path, recorded = get_export_path(directory, draft["draft_id"], item["filename"])
            self.assertEqual(recorded["sha256"], item["sha256"])
            self.assertEqual(path.read_text(), approved["content"])
            path.write_text("corrupt")
            with self.assertRaisesRegex(RuntimeError, "integrity"):
                get_export_path(directory, draft["draft_id"], item["filename"])

    def test_request_revision_allows_edit_but_reject_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            draft = create_draft(directory, {"title": "Revision", "document_type": "proposal"})
            record = review_draft(directory, draft["draft_id"], "request_revision", "Please add a defined objective")
            self.assertEqual(record["status"], "revision_requested")
            edited = edit_draft(directory, draft["draft_id"], record["content"] + "\nObjective: researcher supplied.\n")
            self.assertEqual(edited["status"], "draft")
            rejected = review_draft(directory, draft["draft_id"], "reject", "Scope is not viable")
            with self.assertRaisesRegex(ValueError, "revision requested"):
                edit_draft(directory, draft["draft_id"], rejected["content"] + "more")

    def test_all_exporters_create_valid_file_structures(self):
        with tempfile.TemporaryDirectory() as directory:
            _, claim, _ = self.make_source(directory)
            draft = create_draft(directory, {"title": "A & B (test)", "document_type": "article",
                                             "claim_ids": [claim["claim_id"]]})
            draft = review_draft(directory, draft["draft_id"], "approve", "Template structure reviewed")
            for output_format in ("markdown", "latex", "docx", "pdf"):
                with self.subTest(format=output_format):
                    item = export_draft(directory, draft["draft_id"], output_format)
                    path, _ = get_export_path(directory, draft["draft_id"], item["filename"])
                    if output_format == "docx":
                        with zipfile.ZipFile(path) as archive:
                            self.assertIsNone(archive.testzip())
                            document_xml = archive.read("word/document.xml")
                            ET.fromstring(document_xml)
                            self.assertIn(b"C001", document_xml)
                            for name in archive.namelist():
                                if name.endswith((".xml", ".rels")):
                                    ET.fromstring(archive.read(name))
                    elif output_format == "pdf":
                        reader = PdfReader(path)
                        self.assertGreaterEqual(len(reader.pages), 1)
                        self.assertIn("A & B", reader.pages[0].extract_text())
                        self.assertIn("C001", " ".join(page.extract_text() for page in reader.pages))
                    elif output_format == "latex":
                        text = path.read_text()
                        self.assertIn(r"\documentclass{article}", text)
                        self.assertIn(r"A \& B (test)", text)
                        self.assertIn("C001", text)
                        self.assert_latex_structure(text)
                    else:
                        self.assertIn("# A & B (test)", path.read_text())
                        self.assertIn("C001", path.read_text())

    def test_recorded_evaluation_metrics_are_included_as_actual_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            evaluation_id = "12345678-1234-5678-1234-567812345678"
            folder = Path(directory) / "experiments" / "evaluations"
            folder.mkdir(parents=True)
            (folder / f"{evaluation_id}.json").write_text(json.dumps({"evaluation_id": evaluation_id,
                "dataset_sha256": "a" * 64, "task": "regression", "row_count": 12,
                "metrics": {"mae": 0.25, "rmse": 0.5},
                "visualization": {"type": "actual_predicted_scatter", "points": [[0, 0], [1, 0.5]], "sampled": False},
                "warning": "Descriptive only."}), encoding="utf-8")
            draft = create_draft(directory, {"title": "Measured work", "document_type": "article",
                                             "evaluation_ids": [evaluation_id]})
            self.assertIn("| mae | 0.25 |", draft["content"])
            self.assertEqual(len(draft["figures"]), 1)
            ET.fromstring(draft["figures"][0]["svg"])
            approved = review_draft(directory, draft["draft_id"], "approve", "Checked recorded metrics")
            markdown = export_draft(directory, draft["draft_id"], "markdown")
            latex = export_draft(directory, draft["draft_id"], "latex")
            docx = export_draft(directory, draft["draft_id"], "docx")
            pdf = export_draft(directory, draft["draft_id"], "pdf")
            markdown_path, _ = get_export_path(directory, draft["draft_id"], markdown["filename"])
            latex_path, _ = get_export_path(directory, draft["draft_id"], latex["filename"])
            docx_path, _ = get_export_path(directory, draft["draft_id"], docx["filename"])
            pdf_path, _ = get_export_path(directory, draft["draft_id"], pdf["filename"])
            self.assertIn("<svg", markdown_path.read_text())
            self.assertIn(r"\begin{tabular}", latex_path.read_text())
            self.assertIn(r"\begin{axis}", latex_path.read_text())
            with zipfile.ZipFile(docx_path) as archive:
                document = ET.fromstring(archive.read("word/document.xml"))
                self.assertGreaterEqual(len(document.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tbl")), 2)
                self.assertTrue(any(item.endswith(".svg") for item in archive.namelist()))
                self.assertIn("graphicData", archive.read("word/document.xml").decode())
            pdf_reader = PdfReader(pdf_path)
            self.assertGreaterEqual(len(pdf_reader.pages), 2)
            self.assertIn("mae", " ".join(page.extract_text() for page in pdf_reader.pages))

    def test_changed_or_active_svg_assets_are_rejected_before_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            evaluation_id = "12345678-1234-5678-1234-567812345678"
            folder = Path(directory) / "experiments" / "evaluations"; folder.mkdir(parents=True)
            (folder / f"{evaluation_id}.json").write_text(json.dumps({"evaluation_id": evaluation_id,
                "dataset_sha256": "b" * 64, "task": "regression", "row_count": 2,
                "metrics": {"mae": 0.0}, "visualization": {"type": "actual_predicted_scatter", "points": [[1, 1]]},
                "warning": "Descriptive only."}), encoding="utf-8")
            draft = create_draft(directory, {"title": "Figure", "document_type": "report", "evaluation_ids": [evaluation_id]})
            path = Path(directory) / "writing" / "documents" / draft["draft_id"] / "draft.json"
            record = json.loads(path.read_text()); record["figures"][0]["svg"] = '<svg><script>alert(1)</script></svg>'
            import hashlib
            record["figures"][0]["sha256"] = hashlib.sha256(record["figures"][0]["svg"].encode()).hexdigest()
            path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unsupported or unsafe"):
                get_draft(directory, draft["draft_id"])

    def test_every_supported_document_type_has_a_concrete_section_template(self):
        templates = {
            "article": "## Abstract", "review": "## Review scope", "systematic_review": "## Search and screening",
            "proposal": "## Objectives and questions", "report": "## Executive summary",
            "conference_paper": "## Evaluation and results", "theoretical": "## Conceptual framework",
        }
        with tempfile.TemporaryDirectory() as directory:
            for document_type, heading in templates.items():
                with self.subTest(document_type=document_type):
                    draft = create_draft(directory, {"title": "Template review", "document_type": document_type})
                    self.assertIn(heading, draft["content"])
                    self.assertNotIn("{{", draft["content"])

    def test_confusion_matrix_is_rendered_as_svg_across_supported_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            evaluation_id = "87654321-4321-8765-4321-876543218765"
            folder = Path(directory) / "experiments" / "evaluations"; folder.mkdir(parents=True)
            (folder / f"{evaluation_id}.json").write_text(json.dumps({"evaluation_id": evaluation_id,
                "dataset_sha256": "c" * 64, "task": "classification", "row_count": 3,
                "metrics": {"accuracy": 0.67, "confusion_matrix": [[1, 0], [1, 1]]},
                "visualization": {"type": "confusion_matrix", "labels": ["case", "control"],
                    "matrix": [[1, 0], [1, 1]], "actual_rows_predicted_columns": True},
                "warning": "Descriptive only."}), encoding="utf-8")
            draft = create_draft(directory, {"title": "Classification", "document_type": "report", "evaluation_ids": [evaluation_id]})
            self.assertEqual(draft["figures"][0]["data"]["type"], "confusion_matrix")
            self.assertIn("Actual \\ Predicted", draft["content"])
            review_draft(directory, draft["draft_id"], "approve", "Checked confusion matrix")
            docx = export_draft(directory, draft["draft_id"], "docx")
            path, _ = get_export_path(directory, draft["draft_id"], docx["filename"])
            with zipfile.ZipFile(path) as archive:
                self.assertTrue(any(name.endswith(".svg") for name in archive.namelist()))
                svg_name = next(name for name in archive.namelist() if name.endswith(".svg"))
                ET.fromstring(archive.read(svg_name))

    def assert_latex_structure(self, content):
        environments = []
        for command, environment in re.findall(r"\\(begin|end)\{([A-Za-z*]+)\}", content):
            if command == "begin": environments.append(environment)
            else:
                self.assertTrue(environments, f"Unmatched LaTeX end: {environment}")
                self.assertEqual(environments.pop(), environment)
        self.assertEqual(environments, [])
        depth = 0
        escaped = False
        for char in content:
            if char == "\\" and not escaped:
                escaped = True
                continue
            if not escaped and char == "{": depth += 1
            if not escaped and char == "}": depth -= 1
            self.assertGreaterEqual(depth, 0)
            escaped = False
        self.assertEqual(depth, 0)

    def test_export_rejects_lost_citation_provenance_and_unknown_citation_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            _, reviewed, _ = self.make_source(directory)
            draft = create_draft(directory, {"title": "Citation integrity", "document_type": "review", "claim_ids": [reviewed["claim_id"]]})
            review_draft(directory, draft["draft_id"], "approve", "Ready")
            update_claim_review(directory, reviewed["claim_id"], "provisional", "Review withdrawn")
            with self.assertRaisesRegex(ValueError, "lost reviewed status"):
                export_draft(directory, draft["draft_id"], "pdf")

        with tempfile.TemporaryDirectory() as directory:
            _, reviewed, _ = self.make_source(directory)
            draft = create_draft(directory, {"title": "Missing reference", "document_type": "article", "claim_ids": [reviewed["claim_id"]]})
            content = draft["content"].split("## References", 1)[0] + "## References\nNo source IDs or hashes here.\n"
            edit_draft(directory, draft["draft_id"], content)
            with self.assertRaisesRegex(ValueError, "does not resolve"):
                review_draft(directory, draft["draft_id"], "approve", "Check references")

        with tempfile.TemporaryDirectory() as directory:
            draft = create_draft(directory, {"title": "Bad marker", "document_type": "article"})
            draft = edit_draft(directory, draft["draft_id"], draft["content"] + "\nUnknown [C999].")
            with self.assertRaisesRegex(ValueError, "unresolved citation"):
                review_draft(directory, draft["draft_id"], "approve", "Check references")


if __name__ == "__main__":
    unittest.main()

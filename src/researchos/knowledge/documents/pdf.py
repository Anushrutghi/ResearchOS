"""PDF metadata and page-aware text extraction using pypdf."""

from io import BytesIO
import re
import shutil
import subprocess

from researchos.knowledge.section_extractor import annotate_page_sections, identify_section

MAX_PDF_PAGES = 600
MAX_EXTRACTED_CHARACTERS = 10_000_000
MAX_OCR_PAGES = 100
DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")


def _ocr_empty_pages(content: bytes, pages: list[dict]) -> str:
    empty_indexes = [index for index, page in enumerate(pages) if not page["text"]]
    if not empty_indexes:
        return "not_needed"
    if len(empty_indexes) > MAX_OCR_PAGES:
        return "page_limit_exceeded"
    if not shutil.which("tesseract"):
        return "tesseract_unavailable"
    try:
        import pypdfium2 as pdfium
        from PIL import Image
    except ImportError:
        return "ocr_backend_unavailable"
    try:
        document = pdfium.PdfDocument(content)
        for index in empty_indexes:
            page = document[index]
            bitmap = page.render(scale=2)
            image = bitmap.to_pil().convert("RGB")
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "6"],
                input=buffer.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=20, check=False,
            )
            if result.returncode != 0:
                return "ocr_failed"
            pages[index]["text"] = result.stdout.decode("utf-8", errors="replace").strip()
        return "ocr_extracted" if any(pages[index]["text"] for index in empty_indexes) else "ocr_no_text"
    except subprocess.TimeoutExpired:
        return "ocr_timeout"
    except Exception:
        return "ocr_failed"


def _extract_reference_lines(pages: list[dict]) -> list[dict]:
    references = []
    active_section = None
    for page in pages:
        for line in page.get("text", "").splitlines():
            heading = identify_section(line)
            if heading:
                active_section = heading
                continue
            if active_section == "references" and line.strip():
                raw_text = line.strip()
                doi_match = DOI_PATTERN.search(raw_text)
                year_match = YEAR_PATTERN.search(raw_text)
                references.append({
                    "page": page["page"],
                    "text": raw_text,
                    "raw_text": raw_text,
                    "doi_candidate": doi_match.group(0).rstrip(".,;:") if doi_match else None,
                    "year_candidate": int(year_match.group(0)) if year_match else None,
                    "parse_status": "heuristic_candidate",
                })
    return references


def _split_table_cells(line: str) -> list[str]:
    """Split only obvious text-layout delimiters; never infer missing cells."""
    stripped = line.strip()
    if stripped.count("|") >= 2:
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    elif "\t" in stripped:
        cells = [cell.strip() for cell in re.split(r"\t+", stripped)]
    else:
        cells = [cell.strip() for cell in re.split(r"\s{3,}", stripped)]
    return [cell for cell in cells if cell]


def _table_like_lines(pages: list[dict]) -> list[dict]:
    candidates = []
    for page in pages:
        for line in page.get("text", "").splitlines():
            cells = _split_table_cells(line)
            if len(cells) >= 3 and len(line.strip()) <= 1000:
                candidates.append({"page": page["page"], "text": line.strip(), "cells": cells,
                                   "parse_status": "heuristic_candidate"})
    return candidates


def _group_table_candidates(candidates: list[dict]) -> list[dict]:
    """Group table-like rows by page; columns/headers remain unverified."""
    by_page: dict[int, list[dict]] = {}
    for candidate in candidates:
        by_page.setdefault(candidate["page"], []).append(candidate)
    return [{"page": page, "rows": [item["cells"] for item in rows],
             "raw_rows": [item["text"] for item in rows], "status": "heuristic_candidate",
             "warning": "Text layout may not preserve the original table structure."}
            for page, rows in sorted(by_page.items())]


def extract_pdf(content: bytes, perform_ocr: bool = True) -> dict:
    """Extract page text and basic metadata without inventing missing fields."""
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content), strict=False)
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page processing limit.")
    pages = []
    character_count = 0
    for number, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        character_count += len(text)
        if character_count > MAX_EXTRACTED_CHARACTERS:
            raise ValueError("Extracted PDF text exceeds the processing limit.")
        pages.append({"page": number, "text": text})
    ocr_status = _ocr_empty_pages(content, pages) if perform_ocr else "not_requested"
    pages, sections = annotate_page_sections(pages)
    full_text = "\n".join(page["text"] for page in pages)
    references = _extract_reference_lines(pages)
    table_candidates = _table_like_lines(pages)
    tables = _group_table_candidates(table_candidates)
    metadata = reader.metadata
    title = str(metadata.title).strip() if metadata and metadata.title else None
    authors = [part.strip() for part in re.split(r"[,;]", str(metadata.author)) if part.strip()] if metadata and metadata.author else []
    doi_match = DOI_PATTERN.search(full_text)
    doi = doi_match.group(0).rstrip(".,;:") if doi_match else None
    return {
        "title": title,
        "authors": authors,
        "doi": doi,
        "page_count": len(pages),
        "pages": pages,
        "sections": sections,
        "extraction_status": "text_extracted" if any(page["text"] for page in pages) else "scanned_or_empty",
        "ocr_status": ocr_status,
        "table_candidates": table_candidates,
        "tables": tables,
        "tables_status": "layout_candidates_detected" if table_candidates else "no_table_like_lines_detected",
        "tables_parse_status": "heuristic_row_candidates_detected" if table_candidates else "no_table_like_lines_detected",
        "references": references,
        "references_status": "reference_lines_extracted" if references else ("section_detected_empty" if any(section["name"] == "references" for section in sections) else "not_detected"),
    }

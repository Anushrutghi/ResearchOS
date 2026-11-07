"""Conservative section heading recognition for page-extracted PDF text."""

import re


SECTION_PATTERNS = (
    ("abstract", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?abstract$", re.I)),
    ("introduction", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:introduction|background)$", re.I)),
    ("related_work", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:related work|literature review)$", re.I)),
    ("methodology", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:methods?|methodology|materials and methods|experimental setup)$", re.I)),
    ("results", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:results?|findings)$", re.I)),
    ("discussion", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?discussion$", re.I)),
    ("conclusion", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:conclusions?|future work)$", re.I)),
    ("references", re.compile(r"^(?:\d+(?:\.\d+)*\s+)?(?:references|bibliography)$", re.I)),
)


def identify_section(text: str) -> str | None:
    """Return a recognized heading if the supplied line is an exact heading."""
    candidate = re.sub(r"^[#\s]+|[#\s]+$", "", text.strip())
    candidate = re.sub(r"\s+", " ", candidate)
    for name, pattern in SECTION_PATTERNS:
        if pattern.fullmatch(candidate):
            return name
    return None


def annotate_page_sections(pages: list[dict]) -> tuple[list[dict], list[dict]]:
    active_section = None
    sections = []
    annotated = []
    for page in pages:
        text = page.get("text", "")
        page_sections = []
        for line in text.splitlines():
            recognized = identify_section(line)
            if recognized:
                active_section = recognized
                page_sections.append(recognized)
                sections.append({"name": recognized, "page": page["page"], "heading": line.strip()})
        annotated.append({**page, "section_name": page_sections[-1] if page_sections else active_section})
    return annotated, sections

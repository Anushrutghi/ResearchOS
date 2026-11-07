"""Backward-compatible OpenAlex parsing and search helpers."""

from datetime import datetime, timezone
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from researchos.research.models import Paper, PaperProvenance

OPENALEX_WORKS_URL = "https://api.openalex.org/works"


LiteratureWork = Paper


def _abstract_from_inverted_index(index: dict | None) -> str | None:
    if not index:
        return None
    words: list[str | None] = []
    for word, positions in index.items():
        for position in positions:
            if position >= len(words):
                words.extend([None] * (position + 1 - len(words)))
            words[position] = word
    text = " ".join(word for word in words if word)
    return text or None


def _parse_work(item: dict) -> Paper:
    authors = []
    for authorship in item.get("authorships", []):
        name = (authorship.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
    primary_location = item.get("primary_location") or {}
    source = primary_location.get("source") or {}
    work_id = item.get("id", "")
    return Paper(
        work_id=work_id,
        title=(item.get("title") or "Untitled work").strip(),
        year=item.get("publication_year"),
        authors=authors,
        venue=source.get("display_name"),
        doi=item.get("doi"),
        url=work_id,
        abstract=_abstract_from_inverted_index(item.get("abstract_inverted_index")),
        provenance=[PaperProvenance("OpenAlex", work_id, work_id, datetime.now(timezone.utc))] if work_id else [],
    )


def search_openalex(query: str, limit: int = 10, timeout: float = 20.0) -> list[Paper]:
    """Search OpenAlex. Raises RuntimeError with a readable error on request failures."""
    if not query.strip():
        raise ValueError("Search query must not be empty.")
    if not 1 <= limit <= 100:
        raise ValueError("Result limit must be between 1 and 100.")
    url = f"{OPENALEX_WORKS_URL}?{urlencode({'search': query, 'per-page': limit, 'select': 'id,title,publication_year,authorships,primary_location,doi,abstract_inverted_index'})}"
    request = Request(url, headers={"User-Agent": "ResearchOS/0.2 (open-source research discovery)"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OpenAlex literature search failed: {exc}") from exc
    return [_parse_work(item) for item in payload.get("results", [])]

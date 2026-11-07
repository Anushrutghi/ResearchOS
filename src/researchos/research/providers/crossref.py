"""Crossref REST API adapter using only the Python standard library."""

import json
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from researchos.research.models import Paper, PaperProvenance

CROSSREF_WORKS_URL = "https://api.crossref.org/works"


def _year(item: dict) -> int | None:
    for key in ("published-print", "published-online", "created"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and isinstance(parts[0][0], int):
            return parts[0][0]
    return None


def _parse_work(item: dict) -> Paper:
    doi = item.get("DOI")
    doi_url = f"https://doi.org/{doi}" if doi else ""
    provider_id = doi_url or item.get("URL", "")
    title = (item.get("title") or ["Untitled work"])[0]
    authors = [" ".join(part for part in (author.get("given"), author.get("family")) if part)
               for author in item.get("author", [])]
    authors = [author for author in authors if author]
    abstract = item.get("abstract")
    if abstract:
        abstract = re.sub(r"<[^>]*>", " ", abstract)
        abstract = re.sub(r"\s+", " ", abstract).strip() or None
    return Paper(
        work_id=provider_id,
        title=title.strip() if isinstance(title, str) else "Untitled work",
        year=_year(item), authors=authors,
        venue=(item.get("container-title") or [None])[0],
        doi=doi, url=doi_url or item.get("URL", ""), abstract=abstract,
        source="Crossref",
        provenance=[PaperProvenance("Crossref", provider_id, item.get("URL") or doi_url, datetime.now(timezone.utc))] if provider_id else [],
    )


class CrossrefProvider:
    name = "Crossref"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    def search(self, query: str, limit: int = 10) -> list[Paper]:
        if not query.strip():
            raise ValueError("Search query must not be empty.")
        if not 1 <= limit <= 100:
            raise ValueError("Result limit must be between 1 and 100.")
        url = f"{CROSSREF_WORKS_URL}?{urlencode({'query': query, 'rows': limit})}"
        request = Request(url, headers={"User-Agent": "ResearchOS/0.2 (open-source research discovery)"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Crossref literature search failed: {exc}") from exc
        return [_parse_work(item) for item in payload.get("message", {}).get("items", [])]

"""Multi-provider literature search and DOI-based deduplication."""

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Iterable

from researchos.research.models import (
    Paper, PaperProvenance, ProviderOutcome, ResearchSearchResult, SearchCompleteness,
)
from researchos.research.providers.base import LiteratureProvider
from researchos.research.providers.crossref import CrossrefProvider
from researchos.research.providers.openalex import OpenAlexProvider


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    value = doi.strip().lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    value = value.removeprefix("doi:").strip().rstrip("./")
    return value or None


def deduplicate_papers(papers: Iterable[Paper]) -> list[Paper]:
    result: list[Paper] = []
    by_doi: dict[str, Paper] = {}
    for original in papers:
        paper = deepcopy(original)
        key = normalize_doi(paper.doi)
        if not key or key not in by_doi:
            result.append(paper)
            if key:
                by_doi[key] = paper
            continue
        existing = by_doi[key]
        known = {(p.provider, p.provider_id, p.source_url, p.retrieved_at) for p in existing.provenance}
        for provenance in paper.provenance:
            identity = (provenance.provider, provenance.provider_id, provenance.source_url, provenance.retrieved_at)
            if identity not in known:
                existing.provenance.append(provenance)
                known.add(identity)
        for attr in ("title", "year", "authors", "venue", "url", "abstract"):
            if not getattr(existing, attr) and getattr(paper, attr):
                setattr(existing, attr, getattr(paper, attr))
    return result


def search_literature(
    query: str, limit: int = 10, providers: Iterable[LiteratureProvider] | None = None,
) -> ResearchSearchResult:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Search query must not be empty.")
    if not 1 <= limit <= 100:
        raise ValueError("Result limit must be between 1 and 100.")
    selected = list(providers) if providers is not None else [OpenAlexProvider(), CrossrefProvider()]
    outcomes: list[ProviderOutcome] = []
    all_papers: list[Paper] = []
    for provider in selected:
        try:
            found = provider.search(query, limit)
            retrieved_at = datetime.now(timezone.utc)
            for paper in found:
                if not paper.provenance:
                    paper.provenance.append(PaperProvenance(
                        provider.name, paper.work_id or paper.url, paper.url, retrieved_at
                    ))
            all_papers.extend(found)
            outcomes.append(ProviderOutcome(provider.name, "succeeded", len(found)))
        except Exception as exc:
            outcomes.append(ProviderOutcome(provider.name, "failed", 0, str(exc)))
    papers = deduplicate_papers(all_papers)
    succeeded = sum(outcome.status == "succeeded" for outcome in outcomes)
    if not outcomes or succeeded == 0:
        completeness = SearchCompleteness.FAILED
    elif succeeded < len(outcomes):
        completeness = SearchCompleteness.PARTIAL
    else:
        completeness = SearchCompleteness.COMPLETE
    return ResearchSearchResult(papers, outcomes, len(papers), datetime.now(timezone.utc), completeness)

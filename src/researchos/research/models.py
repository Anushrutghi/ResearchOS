"""Shared, provider-neutral records used by the knowledge search layer."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum


class SearchCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PaperProvenance:
    provider: str
    provider_id: str
    source_url: str
    retrieved_at: datetime

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        data["retrieved_at"] = self.retrieved_at.astimezone(timezone.utc).isoformat()
        return data


@dataclass
class Paper:
    """Normalized paper metadata with all provider records retained."""

    work_id: str
    title: str
    year: int | None
    authors: list[str]
    venue: str | None
    doi: str | None
    url: str
    abstract: str | None = None
    source: str = "OpenAlex"
    provenance: list[PaperProvenance] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["provenance"] = [item.to_dict() for item in self.provenance]
        return data


@dataclass(frozen=True)
class ProviderOutcome:
    provider: str
    status: str
    result_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ResearchSearchResult:
    papers: list[Paper]
    provider_outcomes: list[ProviderOutcome]
    total_unique_papers: int
    search_timestamp: datetime
    completeness: SearchCompleteness

    def to_dict(self) -> dict:
        return {
            "papers": [paper.to_dict() for paper in self.papers],
            "provider_outcomes": [outcome.to_dict() for outcome in self.provider_outcomes],
            "total_unique_papers": self.total_unique_papers,
            "search_timestamp": self.search_timestamp.astimezone(timezone.utc).isoformat(),
            "completeness": self.completeness.value,
        }

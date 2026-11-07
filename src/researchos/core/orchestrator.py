"""Coordinates the implemented ResearchOS workflow."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from researchos.core.request import ResearchRequest
from researchos.documents.markdown import render_report
from researchos.research.models import ResearchSearchResult, SearchCompleteness
from researchos.research.search import search_literature
from researchos.storage.database import save_run


@dataclass(frozen=True)
class ResearchRun:
    run_id: str
    directory: Path
    report_path: Path
    source_count: int
    search_performed: bool


def run_research(
    request: ResearchRequest,
    output_dir: str | Path = "research_runs",
    max_papers: int = 10,
    search: bool = True,
) -> ResearchRun:
    """Run the currently implemented stages and save all artifacts locally."""
    if request.output_format != "markdown":
        raise ValueError(f"Research discovery briefs are currently generated as Markdown. Use the approval-gated writing engine to export a draft as {request.output_format}.")
    if not 1 <= max_papers <= 100:
        raise ValueError("Result limit must be between 1 and 100.")
    result = search_literature(request.topic, limit=max_papers) if search else ResearchSearchResult(
        [], [], 0, datetime.now(timezone.utc), SearchCompleteness.SKIPPED
    )
    folder, payload = save_run(
        request.to_dict(), [paper.to_dict() for paper in result.papers], output_dir,
        search_performed=search,
        provider_outcomes=[outcome.to_dict() for outcome in result.provider_outcomes],
        search_completeness=result.completeness.value,
    )
    report_path = folder / "research_brief.md"
    report_path.write_text(
        render_report(request.to_dict(), payload["works"], payload["run_id"], search_performed=search,
                      provider_outcomes=payload["provider_outcomes"], search_completeness=result.completeness.value),
        encoding="utf-8",
    )
    return ResearchRun(payload["run_id"], folder, report_path, len(result.papers), search)

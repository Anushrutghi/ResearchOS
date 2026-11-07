"""Markdown report generation from recorded work products."""

from datetime import datetime


def render_report(
    request: dict, works: list[dict], run_id: str, search_performed: bool = True,
    provider_outcomes: list[dict] | None = None, search_completeness: str | None = None,
) -> str:
    lines = [
        f"# Research brief: {request['topic']}",
        "",
        f"**Research field:** {request['research_field']}  ",
        f"**Requested format:** {request['output_format']}  ",
        f"**Run ID:** `{run_id}`  ",
        f"**Generated:** {datetime.now().astimezone().isoformat(timespec='minutes')}",
        "",
        "> This is a literature discovery brief. Search results are candidate sources, not a completed systematic review or verified evidence synthesis. No experiment was run and no research finding is asserted.",
        "",
        "## Research question",
        "",
        request["topic"],
        "",
        "## Workflow status",
        "",
        "| Stage | Status |",
        "|---|---|",
        "| Request understanding | Complete |",
        f"| Literature discovery | {'Sources found' if works else ('Skipped by user' if not search_performed else ('Search failed' if search_completeness == 'failed' else 'No sources found'))} |",
        "| Evidence synthesis | Not started |",
        "| Hypothesis and methodology | Not started |",
        "| Experiments and analysis | Not started |",
        "| Critical review and verification | Not started |",
        "| Scientific writing | Not started |",
        "",
        "## Candidate literature",
        "",
    ]
    if not works:
        empty_message = ("All literature providers failed. See provider outcomes in run.json." if search_completeness == "failed" else
                         "No literature records were returned. Check provider outcomes in run.json and search terms." if search_performed else
                         "Literature search was skipped for this run.")
        lines.extend([empty_message, ""])
    if search_completeness == "partial":
        lines.extend(["> Search was partial because one or more providers failed. See provider outcomes in `run.json`.", ""])
    for index, work in enumerate(works, 1):
        authors = ", ".join(work.get("authors", [])) or "Author information unavailable"
        year = work.get("year") or "Year unavailable"
        venue = work.get("venue") or "Venue unavailable"
        lines.extend([
            f"### {index}. {work.get('title', 'Untitled work')}",
            "",
            f"{authors} ({year}). *{venue}.*",
            f"\nSource: [{work.get('source', 'OpenAlex')} record]({work.get('url', '')})" + (f" · [DOI]({work['doi']})" if work.get("doi") else ""),
        ])
        if work.get("abstract"):
            lines.extend(["", f"**Abstract (provider metadata):** {work['abstract']}"])
        lines.append("")
    lines.extend([
        "## Limitations and next steps",
        "",
        "These records were retrieved by a keyword search and have not been screened for relevance, quality, or completeness. Read the source papers, extract claims with page-level citations, define inclusion criteria, and select a domain-appropriate methodology before drawing conclusions.",
        "",
        "## Provenance",
        "",
        "Literature metadata retrieved from the listed scholarly providers. Provider identifiers, source URLs, retrieval times, and outcomes are preserved in `run.json`.",
        "",
    ])
    return "\n".join(lines)

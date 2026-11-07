"""JSON-based local run persistence for the initial release."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid

from researchos.core.workflow import workflow_as_dicts


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "research"


def save_run(
    request: dict, works: list[dict], output_dir: str | Path, search_performed: bool = True,
    provider_outcomes: list[dict] | None = None, search_completeness: str | None = None,
) -> tuple[Path, dict]:
    root = Path(output_dir)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{slugify(request['topic'])}-{uuid.uuid4().hex[:8]}"
    folder = root / run_id
    folder.mkdir(parents=True, exist_ok=False)
    outcomes = provider_outcomes or []
    if search_completeness is None:
        search_completeness = "complete" if works else ("complete" if search_performed else "skipped")
    discovery_status = (
        "skipped_by_user" if not search_performed else
        "failed" if search_completeness == "failed" else
        "partial" if search_completeness == "partial" else
        "completed" if works else "completed_no_results"
    )
    availability = {item["name"]: item["status"] for item in workflow_as_dicts()}
    workflow = [
        {"name": "Request understanding", "status": "completed"},
        {"name": "Literature discovery", "status": discovery_status},
        {"name": "Evidence synthesis", "status": "not_started"},
        {"name": "Hypothesis and methodology", "status": "not_started"},
        {"name": "Experiments and analysis", "status": "not_started"},
        {"name": "Critical review and verification", "status": "not_started"},
        {"name": "Scientific writing", "status": "not_started"},
    ]
    for item in workflow:
        item["availability"] = availability[item["name"]]
    payload = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request": request,
        "workflow": workflow,
        "works": works,
        "provider_outcomes": outcomes,
        "search_completeness": search_completeness,
        "integrity_note": "Discovered literature is unreviewed metadata, not verified evidence. No experiments or scientific conclusions were generated.",
    }
    (folder / "run.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return folder, payload


def list_saved_runs(output_dir: str | Path) -> list[dict]:
    """Read durable run records, skipping malformed folders without hiding their presence elsewhere."""
    root = Path(output_dir)
    if not root.exists(): return []
    records = []
    for path in root.glob("*/run.json"):
        if path.is_symlink() or not path.is_file(): continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("run_id"), str): records.append(value)
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    return sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)

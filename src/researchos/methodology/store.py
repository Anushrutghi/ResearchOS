"""Local JSON storage for editable methodology drafts and researcher approvals."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from researchos.methodology.analyzer import RESEARCH_TYPES
from researchos.methodology.models import StudyDesign
from researchos.knowledge.store import load_graph

DESIGN_FIELDS = {
    "title", "research_field", "research_type", "problem_statement", "objectives", "research_questions",
    "hypotheses", "methodology", "methodology_rationale", "population_or_material", "sample_size",
    "sampling_method", "variables", "conditions", "data_collection", "analysis_plan", "evaluation_metrics", "limitations", "ethics_notes", "evidence_claim_ids",
}
TEXT_LIMITS = {
    "title": 300, "research_field": 200, "problem_statement": 4000, "methodology": 1000,
    "methodology_rationale": 4000, "population_or_material": 2000, "sample_size": 500,
    "sampling_method": 1000, "data_collection": 2000, "analysis_plan": 4000, "ethics_notes": 2000,
}
LIST_LIMITS = {"objectives": 30, "research_questions": 30, "hypotheses": 30, "conditions": 30,
               "evaluation_metrics": 30, "limitations": 30}


def _folder(output_dir: str | Path) -> Path:
    return Path(output_dir) / "methodology" / "designs"


def list_study_designs(output_dir: str | Path) -> list[dict]:
    folder = _folder(output_dir)
    if not folder.exists(): return []
    records = []
    for path in folder.glob("*.json"):
        try: records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError): continue
    return sorted(records, key=lambda item: item.get("updated_at", ""), reverse=True)


def get_study_design(output_dir: str | Path, design_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f-]{36}", design_id): raise ValueError("Invalid study design identifier.")
    path = _folder(output_dir) / f"{design_id}.json"
    if not path.is_file(): raise FileNotFoundError("Study design not found.")
    return json.loads(path.read_text(encoding="utf-8"))


def save_study_design(output_dir: str | Path, payload: dict, design_id: str | None = None) -> dict:
    if not isinstance(payload, dict): raise ValueError("Study design must be an object.")
    if set(payload) - DESIGN_FIELDS: raise ValueError("Study design contains unsupported fields.")
    values = dict(payload)
    for field, maximum in TEXT_LIMITS.items():
        value = values.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > maximum):
            raise ValueError(f"{field.replace('_', ' ').capitalize()} must be {maximum} characters or fewer.")
        values[field] = value.strip() if isinstance(value, str) and value.strip() else None
    if not values.get("title"):
        raise ValueError("Study design title is required.")
    if not values.get("research_field"):
        raise ValueError("Research field is required.")
    if values.get("research_type") not in RESEARCH_TYPES:
        raise ValueError("Choose a supported research type.")
    for field, maximum in LIST_LIMITS.items():
        raw = values.get(field, [])
        if not isinstance(raw, list) or len(raw) > maximum or any(not isinstance(item, str) or len(item) > 1000 for item in raw):
            raise ValueError(f"{field.replace('_', ' ').capitalize()} must be a list of up to {maximum} text items.")
        values[field] = [item.strip() for item in raw if item.strip()]
    variables = values.get("variables", {})
    allowed_roles = {"independent", "dependent", "control", "other"}
    if not isinstance(variables, dict) or len(variables) > 20 or any(
        role not in allowed_roles or not isinstance(items, list) or len(items) > 30
        or any(not isinstance(item, str) or len(item) > 200 for item in items)
        for role, items in variables.items()
    ):
        raise ValueError("Variables must map independent, dependent, control, or other to lists of names.")
    values["variables"] = {role: [item.strip() for item in items if item.strip()] for role, items in variables.items()}
    evidence_claim_ids = values.get("evidence_claim_ids", [])
    if not isinstance(evidence_claim_ids, list) or len(evidence_claim_ids) > 100 or any(
        not isinstance(claim_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", claim_id) for claim_id in evidence_claim_ids
    ):
        raise ValueError("Select up to 100 valid local evidence claim identifiers.")
    known_claim_ids = {claim["claim_id"] for claim in load_graph(output_dir).get("claims", [])}
    if any(claim_id not in known_claim_ids for claim_id in evidence_claim_ids):
        raise ValueError("Every literature evidence claim must exist in the local evidence graph.")
    values["evidence_claim_ids"] = list(dict.fromkeys(evidence_claim_ids))
    if design_id:
        existing = get_study_design(output_dir, design_id)
        if existing["status"] != "draft": raise ValueError("Approved designs are immutable; create a new draft to revise one.")
        values.update({key: existing[key] for key in ("design_id", "created_at", "status")})
    design = StudyDesign(**values)
    design.updated_at = datetime.now(timezone.utc).isoformat()
    record = design.to_dict()
    folder = _folder(output_dir); folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{design.design_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def approve_study_design(output_dir: str | Path, design_id: str, reviewer_note: str) -> dict:
    if not isinstance(reviewer_note, str) or not reviewer_note.strip() or len(reviewer_note) > 2000:
        raise ValueError("Approval requires a reviewer note of 1 to 2000 characters.")
    record = get_study_design(output_dir, design_id)
    if record["status"] != "draft": raise ValueError("Only draft designs can be approved.")
    required = ["problem_statement", "objectives", "research_questions", "methodology", "methodology_rationale",
                "analysis_plan", "limitations", "ethics_notes"]
    if record["research_type"] != "theoretical":
        required.extend(["population_or_material", "sample_size", "sampling_method"])
    if record["research_type"] in {"quantitative", "computational"}:
        required.extend(["evaluation_metrics"])
        if not any(record.get("variables", {}).get(role) for role in ("independent", "dependent", "control", "other")):
            required.append("variables")
    missing = [field for field in required if not record.get(field)]
    graph_claims = {claim["claim_id"]: claim for claim in load_graph(output_dir).get("claims", [])}
    referenced_claims = [graph_claims.get(claim_id) for claim_id in record.get("evidence_claim_ids", [])]
    if not referenced_claims or any(claim is None or claim.get("verification_status") not in {"reviewed", "verified"}
                                    for claim in referenced_claims):
        missing.append("reviewed_literature_evidence_claims")
    if missing:
        raise ValueError("Complete these fields before approval: " + ", ".join(missing))
    record["status"] = "approved"
    record["reviewer_note"] = reviewer_note.strip()
    record["approved_at"] = datetime.now(timezone.utc).isoformat()
    record["updated_at"] = record["approved_at"]
    (_folder(output_dir) / f"{design_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record

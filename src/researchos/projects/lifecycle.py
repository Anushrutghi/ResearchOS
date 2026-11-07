"""Evidence-preserving view of the records attached to a project."""

from researchos.experiments.analysis import list_evaluations
from researchos.experiments.store import get_experiment
from researchos.experiments.datasets import get_dataset
from researchos.knowledge.store import load_graph
from researchos.methodology.store import get_study_design
from researchos.writing.store import get_draft
from researchos.workspace import list_documents
from researchos.storage.database import list_saved_runs


def project_lifecycle(output_dir, project: dict) -> dict:
    references = []
    for collection in project.get("collections", []):
        for item in collection.get("records", []):
            references.append({**item, "collection_id": collection.get("collection_id"),
                               "collection_title": collection.get("title")})
    valid, stale = [], []
    graph_claims = {item.get("claim_id"): item for item in load_graph(output_dir).get("claims", [])}
    evaluation_ids = {item.get("evaluation_id") for item in list_evaluations(output_dir)}
    saved_runs = list_saved_runs(output_dir)
    runs = {item.get("run_id") for item in saved_runs}
    papers = {item.get("document_id") for item in list_documents(output_dir)}
    for run in saved_runs:
        papers.update(item.get("work_id") for item in run.get("works", []) if isinstance(item, dict))
    for ref in references:
        kind, record_id = ref["record_type"], ref["record_id"]
        try:
            if kind == "research_run": exists = record_id in runs
            elif kind == "paper": exists = record_id in papers
            elif kind == "claim": exists = record_id in graph_claims
            elif kind == "study_design": exists = get_study_design(output_dir, record_id) is not None
            elif kind == "dataset": exists = get_dataset(output_dir, record_id) is not None
            elif kind == "experiment": exists = get_experiment(output_dir, record_id) is not None
            elif kind == "evaluation": exists = record_id in evaluation_ids
            elif kind == "writing_draft": exists = get_draft(output_dir, record_id) is not None
            else: exists = False
        except (ValueError, FileNotFoundError, OSError):
            exists = False
        (valid if exists else stale).append(ref)

    by_type = {}
    for item in valid:
        by_type.setdefault(item["record_type"], []).append(item["record_id"])
    claims = [graph_claims[item] for item in by_type.get("claim", [])]
    designs, experiments, drafts = [], [], []
    for record_id in by_type.get("study_design", []):
        try: designs.append(get_study_design(output_dir, record_id))
        except (ValueError, FileNotFoundError, OSError): pass
    for record_id in by_type.get("experiment", []):
        try: experiments.append(get_experiment(output_dir, record_id))
        except (ValueError, FileNotFoundError, OSError): pass
    for record_id in by_type.get("writing_draft", []):
        try: drafts.append(get_draft(output_dir, record_id))
        except (ValueError, FileNotFoundError, OSError): pass

    def stage(stage_id, title, needed, note, state):
        ids = sorted(set(record_id for kind in needed for record_id in by_type.get(kind, [])))
        status = state(ids) if callable(state) else ("has_records" if ids else "not_started")
        return {"stage": stage_id, "title": title, "status": status, "record_ids": ids, "note": note}

    stages = [
        stage("discovery", "Literature discovery", {"research_run"},
              "Search output is candidate metadata; a saved run does not validate evidence.", None),
        stage("evidence", "Source-linked evidence review", {"paper", "claim"},
              "Claims remain provisional until a researcher checks their cited source passage.",
              lambda ids: "reviewed" if ids and all(item.get("verification_status") in {"reviewed", "verified"} for item in claims) else "review_required" if ids else "not_started"),
        stage("study_design", "Methodology and study design", {"study_design"},
              "Design approval is a researcher decision and does not establish feasibility.",
              lambda ids: "approved" if any(item.get("status") == "approved" for item in designs) else "draft" if ids else "not_started"),
        stage("experiment", "Experiment execution", {"dataset", "experiment"},
              "Execution records must report the configured isolated backend and preserve failure states.",
              lambda ids: "succeeded" if any(item.get("status") == "succeeded" for item in experiments) else "failed" if any(item.get("status") == "failed" for item in experiments) else "not_started"),
        stage("evaluation", "Descriptive evaluation", {"evaluation"},
              "Metrics describe supplied rows only; they do not establish generalization or significance.", None),
        stage("writing", "Research writing", {"writing_draft"},
              "Exports require approval of the exact draft content and retain source citation checks.",
              lambda ids: "approved" if any(item.get("status") == "approved" for item in drafts) else "draft" if ids else "not_started"),
        {"stage": "review", "title": "Project review", "status": "decision_recorded" if any(item.get("decision") in {"approve", "reject", "request_revision"} for item in project.get("reviews", [])) else "feedback_recorded" if project.get("reviews") else "not_started",
         "record_ids": [item.get("review_id") for item in project.get("reviews", [])],
         "note": "Review decisions are attributable project records; they do not certify scientific validity."},
    ]
    return {"project_id": project["project_id"], "revision": project.get("revision"),
            "collection_count": len(project.get("collections", [])), "record_count": len(references),
            "valid_record_count": len(valid), "stale_references": stale, "stages": stages,
            "limitations": ["The lifecycle view summarizes only records explicitly attached to project collections.",
                "ResearchOS does not automatically combine records into scientific conclusions or decide when a study is valid.",
                "No project stage is considered complete solely because a later-stage record exists."]}

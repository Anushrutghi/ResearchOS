"""Source-linked, researcher-editable writing drafts with export approval gates."""

from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import re
import stat
import uuid
import xml.etree.ElementTree as ET

from researchos.experiments.store import get_experiment
from researchos.experiments.analysis import list_evaluations
from researchos.knowledge.store import load_graph
from researchos.methodology.store import get_study_design
from researchos.workspace import list_documents
from researchos.writing.exporters import export_content


DOCUMENT_TYPES = {"article", "review", "systematic_review", "proposal", "report", "conference_paper", "theoretical"}
MAX_DRAFT_BYTES = 2_000_000
TEMPLATE_DIR = Path(__file__).with_name("templates")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def writing_catalog(output_dir: str | Path) -> dict:
    claims = load_graph(output_dir).get("claims", [])
    documents = {item["document_id"]: item for item in list_documents(output_dir)}
    claim_options = []
    for claim in claims:
        source = claim.get("source", {})
        document = documents.get(source.get("document_id"))
        if document:
            claim_options.append({"claim_id": claim["claim_id"], "text": claim.get("original_text", ""),
                "verification_status": claim.get("verification_status", "provisional"),
                "filename": document.get("filename"), "page_number": source.get("page_number")})
    designs = [item for item in _list_designs(output_dir) if item.get("status") == "approved"]
    experiments = [item for item in _list_experiments(output_dir) if item.get("status") == "succeeded"]
    evaluations = list_evaluations(output_dir)
    return {"claims": claim_options,
            "designs": [{"design_id": item["design_id"], "title": item.get("title")} for item in designs],
            "experiments": [{"experiment_id": item["experiment_id"], "title": item.get("title")} for item in experiments],
            "evaluations": [{"evaluation_id": item["evaluation_id"], "task": item.get("task"),
                             "row_count": item.get("row_count")} for item in evaluations]}


def _list_designs(output_dir):
    from researchos.methodology.store import list_study_designs
    return list_study_designs(output_dir)


def _list_experiments(output_dir):
    from researchos.experiments.store import list_experiments
    return list_experiments(output_dir)


def create_draft(output_dir: str | Path, payload: dict) -> dict:
    allowed = {"title", "document_type", "research_question", "claim_ids", "design_ids", "experiment_ids", "evaluation_ids"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Writing request contains unsupported fields.")
    title = payload.get("title")
    document_type = payload.get("document_type")
    question = payload.get("research_question", "")
    if not isinstance(title, str) or not title.strip() or len(title) > 300:
        raise ValueError("Document title is required and limited to 300 characters.")
    if document_type not in DOCUMENT_TYPES:
        raise ValueError("Choose a supported document type.")
    if not isinstance(question, str) or len(question) > 4000:
        raise ValueError("Research question must be text no longer than 4000 characters.")
    selections = {}
    limits = {"claim_ids": 100, "design_ids": 50, "experiment_ids": 100, "evaluation_ids": 100}
    for field, maximum in limits.items():
        values = payload.get(field, [])
        if not isinstance(values, list) or len(values) > maximum or any(not isinstance(item, str) for item in values):
            raise ValueError(f"{field} must contain at most {maximum} identifiers.")
        if len(values) != len(set(values)):
            raise ValueError(f"{field} must not contain duplicate identifiers.")
        selections[field] = values

    documents = {item["document_id"]: item for item in list_documents(output_dir)}
    graph = load_graph(output_dir)
    claims_by_id = {item["claim_id"]: item for item in graph.get("claims", [])}
    citations = []
    for index, claim_id in enumerate(selections["claim_ids"], 1):
        claim = claims_by_id.get(claim_id)
        if not claim:
            raise ValueError(f"Selected claim {claim_id} is not in the local evidence graph.")
        source = claim.get("source", {})
        document = documents.get(source.get("document_id"))
        if not document:
            raise ValueError(f"Source document for claim {claim_id} is missing.")
        file_path = _source_document_path(output_dir, document)
        if not file_path.is_file() or hashlib.sha256(file_path.read_bytes()).hexdigest() != document.get("sha256"):
            raise ValueError(f"Source document for claim {claim_id} failed its content-integrity check.")
        status = claim.get("verification_status", "provisional")
        citation_id = f"C{index:03d}"
        citations.append({"citation_id": citation_id, "claim_id": claim_id,
            "document_id": document["document_id"], "filename": document.get("filename"),
            "document_sha256": document["sha256"], "page_number": source.get("page_number"),
            "verification_status": status, "verified": status in {"reviewed", "verified"},
            "claim_text": claim.get("original_text", ""), "source_passage": source.get("text", "")})

    designs = []
    for design_id in selections["design_ids"]:
        design = get_study_design(output_dir, design_id)
        if design.get("status") != "approved":
            raise ValueError("Only approved study designs can be included in a draft.")
        designs.append({"design_id": design_id, "title": design.get("title"), "research_type": design.get("research_type"),
            "methodology": design.get("methodology"), "methodology_rationale": design.get("methodology_rationale"),
            "population_or_material": design.get("population_or_material"), "sample_size": design.get("sample_size"),
            "sampling_method": design.get("sampling_method"), "data_collection": design.get("data_collection"),
            "analysis_plan": design.get("analysis_plan"), "evaluation_metrics": design.get("evaluation_metrics", []),
            "limitations": design.get("limitations", []), "evidence_claim_ids": design.get("evidence_claim_ids", []),
            "approved_at": design.get("approved_at")})

    experiments = []
    for experiment_id in selections["experiment_ids"]:
        experiment = get_experiment(output_dir, experiment_id)
        if experiment.get("status") != "succeeded":
            raise ValueError("Only successful experiment records can be included in a writing draft.")
        execution = experiment.get("execution") or {}
        experiments.append({"experiment_id": experiment_id, "title": experiment.get("title"),
            "source_sha256": experiment.get("source_sha256"), "dataset_sha256": experiment.get("dataset_sha256", []),
            "seed": experiment.get("seed"), "image": execution.get("image"),
            "stdout": execution.get("stdout", "")[:100_000], "stderr": execution.get("stderr", "")[:20_000],
            "resource_usage": execution.get("resource_usage"), "completed_at": experiment.get("completed_at")})

    available_evaluations = {item["evaluation_id"]: item for item in list_evaluations(output_dir)}
    evaluations = []
    for evaluation_id in selections["evaluation_ids"]:
        evaluation = available_evaluations.get(evaluation_id)
        if not evaluation:
            raise ValueError(f"Prediction evaluation {evaluation_id} is not available.")
        evaluations.append({"evaluation_id": evaluation_id, "dataset_sha256": evaluation.get("dataset_sha256"),
            "task": evaluation.get("task"), "row_count": evaluation.get("row_count"),
            "metrics": evaluation.get("metrics"), "visualization": evaluation.get("visualization"),
            "warning": evaluation.get("warning")})

    sections = _build_sections(title.strip(), question.strip(), document_type, citations, designs, experiments, evaluations)
    figures = sections.pop("figures", [])
    template_path = TEMPLATE_DIR / f"{document_type}.md.tpl"
    template = template_path.read_text(encoding="utf-8")
    content = template
    for key, value in sections.items():
        if key in {"missing_sections", "figures"}:
            continue
        content = content.replace("{{" + key + "}}", value)
    if "{{" in content:
        raise RuntimeError("Document template contains an unresolved field.")
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_DRAFT_BYTES:
        raise ValueError("Generated draft exceeds the 2 MB limit.")
    draft_id = str(uuid.uuid4())
    record = {"draft_id": draft_id, "title": title.strip(), "document_type": document_type,
        "research_question": question.strip(), "status": "draft", "content": content,
        "content_sha256": hashlib.sha256(content_bytes).hexdigest(), "citations": citations,
        "study_designs": designs, "experiments": experiments, "evaluations": evaluations,
        "figures": figures,
        "missing_sections": [name for name in sections.get("missing_sections", [])],
        "provenance": {"claim_ids": selections["claim_ids"], "design_ids": selections["design_ids"],
                       "experiment_ids": selections["experiment_ids"], "evaluation_ids": selections["evaluation_ids"]},
        "created_at": _now(), "updated_at": _now(), "approval": None, "revisions": [], "exports": []}
    _write(output_dir, record)
    return record


def _build_sections(title, question, document_type, citations, designs, experiments, evaluations):
    missing = []
    figures = []
    section_names = {
        "abstract": {"proposal": "Summary", "report": "Executive summary"},
        "introduction": {"review": "Evidence overview", "proposal": "Rationale and evidence",
            "report": "Background", "systematic_review": "Evidence synthesis", "theoretical": "Problem and prior evidence"},
        "methods": {"systematic_review": "Search and screening", "proposal": "Proposed methods", "theoretical": "Conceptual framework"},
        "results": {"review": "Synthesis notes", "systematic_review": "Results", "proposal": "Expected outcomes",
            "report": "Recorded results", "conference_paper": "Evaluation and results", "theoretical": "Argument and implications"},
        "discussion": {"report": "Discussion", "conference_paper": "Discussion and limitations"},
        "limitations": {"proposal": "Feasibility and ethics", "report": "Limitations and next steps",
            "conference_paper": "Discussion and limitations"},
    }
    section_label = lambda key, fallback: section_names.get(key, {}).get(document_type, fallback)
    intro = []
    if question:
        intro.append(f"Research question supplied by the researcher: {question}")
    if citations:
        intro.append("Selected source-linked statements (quoted from researcher-reviewed records; review status is shown):")
        for item in citations:
            mark = f"[NEEDS REVIEW: {item['citation_id']}]" if not item["verified"] else f"[{item['citation_id']}]"
            intro.append(f"> {item['claim_text']} {mark}\n> — {item['filename']}, p. {item['page_number']} (claim status: {item['verification_status']})")
    if not intro:
        intro.append("[Researcher draft required. No source-linked claims or research question were selected.]")
        missing.append(section_label("introduction", "Introduction / source-grounded rationale"))

    methods = []
    for design in designs:
        methods.append(f"### {design['title']} (approved {design['approved_at']})")
        for label, key in (("Research type", "research_type"), ("Methodology", "methodology"),
                           ("Rationale", "methodology_rationale"), ("Population or material", "population_or_material"),
                           ("Sample size", "sample_size"), ("Sampling", "sampling_method"),
                           ("Data collection", "data_collection"), ("Analysis plan", "analysis_plan")):
            if design.get(key): methods.append(f"- **{label}:** {design[key]}")
        if design.get("evaluation_metrics"): methods.append("- **Evaluation metrics:** " + "; ".join(design["evaluation_metrics"]))
        if design.get("limitations"): methods.append("- **Recorded limitations:** " + "; ".join(design["limitations"]))
    if not methods:
        methods.append("[Researcher draft required. No approved study design was selected.]")
        missing.append(section_label("methods", "Methods"))

    results = []
    for evaluation in evaluations:
        flattened = _flatten_metrics(evaluation["metrics"])
        results.extend([f"### Descriptive evaluation · {evaluation['task']} · {evaluation['row_count']} rows",
            f"Dataset SHA-256: `{evaluation['dataset_sha256']}`", "Metrics computed from supplied rows (not an inferential or generalization claim):",
            "\n".join(["| Metric | Recorded value |", "|---|---|"] + [f"| {name} | {value} |" for name, value in flattened])])
        results.extend(["", "Full metric record:", "```json", json.dumps(evaluation["metrics"], ensure_ascii=False, indent=2), "```", evaluation["warning"]])
        visualization = evaluation.get("visualization") or {}
        if visualization.get("type") == "confusion_matrix":
            labels = visualization.get("labels", [])
            matrix = visualization.get("matrix", [])
            if labels and len(labels) <= 50 and len(matrix) == len(labels):
                header = "| Actual \\ Predicted | " + " | ".join(str(label).replace("|", "\\|") for label in labels) + " |"
                separator = "|---|" + "|".join("---" for _ in labels) + "|"
                rows = [header, separator]
                rows.extend("| " + str(label).replace("|", "\\|") + " | " + " | ".join(str(value) for value in row) + " |"
                            for label, row in zip(labels, matrix) if len(row) == len(labels))
                results.extend(["Confusion matrix (actual rows, predicted columns):", "\n".join(rows)])
        elif visualization.get("type") == "actual_predicted_scatter":
            points = visualization.get("points", [])[:30]
            if points:
                table = ["| Sample | Actual | Predicted |", "|---:|---:|---:|"]
                table.extend(f"| {index} | {point[0]} | {point[1]} |" for index, point in enumerate(points, 1)
                             if isinstance(point, list) and len(point) == 2)
                results.extend(["Actual versus predicted sample points (deterministic, capped at 30 rows):", "\n".join(table)])
        figure = _make_figure(visualization, len(figures) + 1)
        if figure:
            figures.append(figure)
            results.append(f"[[FIGURE:{figure['figure_id']}]]")
    for experiment in experiments:
        results.extend([f"### Recorded execution · {experiment['title']}",
            f"Source SHA-256: `{experiment['source_sha256']}` · seed: `{experiment['seed']}` · image: `{experiment['image']}`",
            f"Input dataset SHA-256: {', '.join('`'+item+'`' for item in experiment['dataset_sha256']) or 'none'}",
            "The following is captured program output, not an independently validated scientific finding:",
            "```text", experiment["stdout"] or "(no standard output)", "```"])
    if not results:
        results.append("[Researcher draft required. No successful experiment or supplied-row evaluation was selected; no results have been invented.]")
        missing.append(section_label("results", "Results"))

    references = []
    for citation in citations:
        review_flag = " — verify claim/source alignment" if not citation["verified"] else ""
        references.append(f"[{citation['citation_id']}] {citation['filename']}, page {citation['page_number']} · "
            f"stored PDF SHA-256 `{citation['document_sha256']}` · source document `{citation['document_id']}`{review_flag}.")
    if not references:
        references.append("[No local PDF references selected. Add and review sources before making literature claims.]")
        missing.append("References")

    placeholders = {
        "abstract": "[Researcher draft required. No abstract or conclusions have been generated.]",
        "scope": f"Research question supplied by researcher: {question}" if question else "[Researcher must define the scope and eligibility criteria.]",
        "discussion": "[Researcher draft required. Interpret findings only after reviewing the approved records.]",
        "limitations": "[Researcher draft required. Record design, data, and analysis limitations.]",
    }
    for key in ("abstract", "discussion", "limitations"):
        if key == "discussion" and document_type not in {"article", "report"}:
            continue
        fallback = {"abstract": "Abstract", "discussion": "Discussion", "limitations": "Limitations"}[key]
        missing.append(section_label(key, fallback))
    if not question and document_type in {"review", "systematic_review", "proposal"}:
        missing.append(section_label("scope", "Scope"))
    placeholders.update({"title": title, "introduction": "\n\n".join(intro),
        "methods": "\n\n".join(methods), "results": "\n\n".join(results),
        "references": "\n".join(references), "missing_sections": missing, "figures": figures})
    return placeholders


def _make_figure(visualization: dict, number: int) -> dict | None:
    kind = visualization.get("type")
    figure_id = f"FIG{number:03d}"
    if kind == "confusion_matrix":
        labels, matrix = visualization.get("labels", []), visualization.get("matrix", [])
        if not isinstance(labels, list) or not 1 <= len(labels) <= 20 or not isinstance(matrix, list) or len(matrix) != len(labels):
            return None
        if any(not isinstance(label, str) or len(label) > 50 for label in labels) or any(
            not isinstance(row, list) or len(row) != len(labels) or
            any(not isinstance(cell, int) or isinstance(cell, bool) or cell < 0 for cell in row) for row in matrix):
            return None
        title = "Confusion matrix (actual rows, predicted columns)"
        left, top, cell = 70, 50, 36
        width, height = left + cell * len(labels) + 20, top + cell * len(labels) + 35
        maximum = max((value for row in matrix for value in row), default=1) or 1
        rows = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title, quote=True)}">',
                f'<text x="{left}" y="24" font-family="sans-serif" font-size="14">{escape(title)}</text>']
        for r, row in enumerate(matrix):
            for c, value in enumerate(row):
                gray = round(250 - 170 * value / maximum)
                x, y = left + c * cell, top + r * cell
                rows.extend([f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="rgb({gray},{gray},{gray})" stroke="#667085"/>',
                    f'<text x="{x+cell/2}" y="{y+cell/2+4}" text-anchor="middle" font-family="sans-serif" font-size="11">{value}</text>'])
            label = escape(labels[r][:20])
            rows.append(f'<text x="{left-6}" y="{top+r*cell+cell/2+4}" text-anchor="end" font-family="sans-serif" font-size="10">{label}</text>')
        for c, label in enumerate(labels):
            rows.append(f'<text x="{left+c*cell+cell/2}" y="{top+len(labels)*cell+14}" text-anchor="middle" font-family="sans-serif" font-size="10">{escape(label[:20])}</text>')
        rows.append("</svg>")
        svg = "".join(rows)
        data = {"type": kind, "labels": labels, "matrix": matrix}
    elif kind == "actual_predicted_scatter":
        points = visualization.get("points", [])
        valid = []
        if not isinstance(points, list): return None
        for point in points[:200]:
            if not isinstance(point, list) or len(point) != 2: continue
            if any(not isinstance(number, (int, float)) or isinstance(number, bool) or abs(number) > 1e150 for number in point): continue
            valid.append([float(point[0]), float(point[1])])
        if not valid: return None
        title = "Actual versus predicted values (deterministic sample)"
        minimum = min(number for pair in valid for number in pair)
        maximum = max(number for pair in valid for number in pair)
        span = maximum - minimum or 1
        left, top, width, height = 55, 40, 440, 280
        circles = []
        for actual, predicted in valid:
            x = left + (actual - minimum) / span * width
            y = top + height - (predicted - minimum) / span * height
            circles.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="#2878b5"/>')
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {left+width+20} {top+height+45}" role="img" aria-label="{escape(title, quote=True)}">'
            f'<text x="{left}" y="20" font-family="sans-serif" font-size="14">{escape(title)}</text>'
            f'<path d="M {left} {top+height} H {left+width} M {left} {top} V {top+height}" stroke="#344054" fill="none"/>'
            f'<path d="M {left} {top+height} L {left+width} {top}" stroke="#999" stroke-dasharray="5 4"/>'
            + "".join(circles) + f'<text x="{left+width/2}" y="{top+height+32}" text-anchor="middle" font-family="sans-serif">Actual</text>'
            + f'<text x="14" y="{top+height/2}" transform="rotate(-90 14 {top+height/2})" text-anchor="middle" font-family="sans-serif">Predicted</text></svg>')
        data = {"type": kind, "points": valid, "sampled": visualization.get("sampled", False)}
    else:
        return None
    return {"figure_id": figure_id, "title": title, "alt_text": title, "svg": svg,
            "data": data, "sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest()}


def _flatten_metrics(value, prefix=""):
    rows = []
    if isinstance(value, dict):
        for key, item in value.items():
            rows.extend(_flatten_metrics(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        rows.append((prefix, json.dumps(value, ensure_ascii=False, separators=(",", ":"))))
    else:
        rows.append((prefix, json.dumps(value, ensure_ascii=False)))
    return rows


def list_drafts(output_dir: str | Path) -> list[dict]:
    folder = Path(output_dir) / "writing" / "documents"
    if not folder.exists(): return []
    values = []
    for path in folder.glob("*/draft.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            record.pop("content", None)
            values.append(record)
        except (OSError, ValueError):
            continue
    return sorted(values, key=lambda item: item.get("updated_at", ""), reverse=True)


def get_draft(output_dir: str | Path, draft_id: str) -> dict:
    if not isinstance(draft_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", draft_id):
        raise ValueError("Invalid writing draft identifier.")
    path = Path(output_dir) / "writing" / "documents" / draft_id / "draft.json"
    if not path.is_file(): raise FileNotFoundError("Writing draft not found.")
    record = json.loads(path.read_text(encoding="utf-8"))
    if hashlib.sha256(record["content"].encode("utf-8")).hexdigest() != record.get("content_sha256"):
        raise RuntimeError("Writing draft failed its content-integrity check.")
    _verify_figure_assets(record.get("figures", []))
    return record


def edit_draft(output_dir: str | Path, draft_id: str, content: str) -> dict:
    record = get_draft(output_dir, draft_id)
    if record["status"] not in {"draft", "revision_requested"}: raise ValueError("Only drafts with researcher revision requested can be changed.")
    if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > MAX_DRAFT_BYTES:
        raise ValueError("Draft content must contain 1 to 2000000 UTF-8 bytes.")
    record["revisions"].append({"content_sha256": record["content_sha256"], "saved_at": _now()})
    record["content"] = content
    record["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    record["status"] = "draft"
    record["approval"] = None
    record["updated_at"] = _now()
    _write(output_dir, record)
    return record


def review_draft(output_dir: str | Path, draft_id: str, action: str, note: str) -> dict:
    record = get_draft(output_dir, draft_id)
    if record["status"] != "draft": raise ValueError("Only a draft can be reviewed.")
    if action not in {"approve", "reject", "request_revision"}:
        raise ValueError("Review action must be approve, reject, or request_revision.")
    if not isinstance(note, str) or not note.strip() or len(note) > 2000:
        raise ValueError("A researcher review note of 1 to 2000 characters is required.")
    if action == "approve":
        _verify_citations(output_dir, record)
    record["status"] = {"approve": "approved", "reject": "rejected", "request_revision": "revision_requested"}[action]
    record["approval"] = {"action": action, "note": note.strip(), "content_sha256": record["content_sha256"], "reviewed_at": _now()}
    record["updated_at"] = _now()
    _write(output_dir, record)
    return record


def _write(output_dir: str | Path, record: dict) -> None:
    folder = Path(output_dir) / "writing" / "documents" / record["draft_id"]
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / "draft.json.tmp"
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(folder / "draft.json")
    content_path = folder / "draft.md"
    content_path.write_text(record["content"], encoding="utf-8")


def export_draft(output_dir: str | Path, draft_id: str, output_format: str) -> dict:
    record = get_draft(output_dir, draft_id)
    if record["status"] != "approved" or record.get("approval", {}).get("action") != "approve":
        raise ValueError("A researcher must approve the current draft before export.")
    if record["approval"].get("content_sha256") != record["content_sha256"]:
        raise ValueError("The draft content changed after approval; review the current version before export.")
    _verify_citations(output_dir, record)
    extension, content, content_type = export_content(record["content"], output_format, record.get("figures", []))
    if len(content) > 20_000_000:
        raise ValueError("Export exceeds the 20 MB limit.")
    folder = Path(output_dir) / "writing" / "documents" / draft_id / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    number = 1 + max((item.get("version", 0) for item in record["exports"] if item.get("format") == output_format), default=0)
    filename = f"{record['document_type']}-v{number}.{extension}"
    path = folder / filename
    with path.open("xb") as destination:
        destination.write(content)
    item = {"format": output_format, "filename": filename, "version": number,
            "byte_size": len(content), "sha256": hashlib.sha256(content).hexdigest(),
            "content_type": content_type, "draft_content_sha256": record["content_sha256"],
            "exported_at": _now()}
    record["exports"].append(item)
    record["updated_at"] = _now()
    _write(output_dir, record)
    return item


def get_export_path(output_dir: str | Path, draft_id: str, filename: str) -> tuple[Path, dict]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", filename):
        raise ValueError("Invalid export filename.")
    record = get_draft(output_dir, draft_id)
    item = next((entry for entry in record.get("exports", []) if entry.get("filename") == filename), None)
    if item is None: raise FileNotFoundError("Writing export not found.")
    path = Path(output_dir) / "writing" / "documents" / draft_id / "exports" / filename
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size != item.get("byte_size") or \
            hashlib.sha256(path.read_bytes()).hexdigest() != item.get("sha256"):
        raise RuntimeError("Writing export failed its content-integrity check.")
    return path, item


def _verify_citations(output_dir: str | Path, record: dict) -> None:
    documents = {item["document_id"]: item for item in list_documents(output_dir)}
    claims = {item["claim_id"]: item for item in load_graph(output_dir).get("claims", [])}
    known = {item["citation_id"] for item in record.get("citations", [])}
    tokens = set(re.findall(r"\[(?:NEEDS REVIEW: )?(C\d{3})\]", record["content"]))
    if tokens - known:
        raise ValueError("Draft contains an unresolved citation identifier.")
    references_match = re.search(r"(?im)^##\s+References\s*$([\s\S]*)", record["content"])
    references_text = references_match.group(1) if references_match else ""
    for citation in record.get("citations", []):
        if citation["citation_id"] not in references_text or citation["document_sha256"] not in references_text or \
                citation["document_id"] not in references_text:
            raise ValueError(f"Citation {citation['citation_id']} does not resolve in the draft's References section.")
        document = documents.get(citation["document_id"])
        claim = claims.get(citation["claim_id"])
        if not document or not claim or claim.get("source", {}).get("document_id") != citation["document_id"]:
            raise ValueError("A cited claim no longer resolves to its stored source record.")
        path = _source_document_path(output_dir, document)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != citation["document_sha256"]:
            raise ValueError("A cited source document failed its content-integrity check.")
        if claim.get("verification_status") == "rejected":
            raise ValueError("A cited claim was rejected after draft creation; revise and review the draft again.")
        if citation.get("verified") and claim.get("verification_status") not in {"reviewed", "verified"}:
            raise ValueError("A cited claim lost reviewed status after draft creation; create and review a new draft.")


def _source_document_path(output_dir: str | Path, document: dict) -> Path:
    document_id = document.get("document_id", "")
    if not re.fullmatch(r"[0-9a-f]{64}", document_id):
        raise ValueError("Cited source record has an invalid document identifier.")
    path = Path(output_dir) / "documents" / f"{document_id}.pdf"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Cited source must be a stored regular PDF file.")
    return path


def _verify_figure_assets(figures: list[dict]) -> None:
    if not isinstance(figures, list) or len(figures) > 20:
        raise RuntimeError("Writing draft contains an invalid number of figures.")
    total = 0
    allowed_tags = {"svg", "g", "rect", "text", "circle", "path"}
    for figure in figures:
        svg = figure.get("svg") if isinstance(figure, dict) else None
        if not isinstance(svg, str):
            raise RuntimeError("Writing figure is malformed.")
        encoded = svg.encode("utf-8")
        total += len(encoded)
        if total > 1_000_000 or hashlib.sha256(encoded).hexdigest() != figure.get("sha256"):
            raise RuntimeError("Writing figure failed its size or integrity check.")
        try:
            root = ET.fromstring(encoded)
        except ET.ParseError as exc:
            raise RuntimeError("Writing figure is invalid SVG XML.") from exc
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] not in allowed_tags or any(
                    key.casefold().startswith("on") or key.rsplit("}", 1)[-1] in {"href", "style"}
                    for key in element.attrib):
                raise RuntimeError("Writing figure contains unsupported or unsafe SVG content.")

"""Portable JSON persistence for claims and a lightweight evidence graph."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from researchos.knowledge.models import (
    EvidenceCategory, KnowledgeEdge, KnowledgeNode, NodeType, Relationship,
    ResearchClaim, SourcePassage, VerificationStatus,
)
from researchos.workspace import list_documents


def _path(output_dir: str | Path) -> Path:
    return Path(output_dir) / "knowledge" / "evidence_graph.json"


def load_graph(output_dir: str | Path) -> dict:
    path = _path(output_dir)
    if not path.exists():
        return {"schema_version": 1, "nodes": [], "edges": [], "claims": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_graph(output_dir: str | Path, graph: dict) -> None:
    path = _path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _ensure_edge(graph: dict, source_id: str, target_id: str, relationship: Relationship,
                 status: str = "proposed", notes: str = "") -> None:
    if not any(edge["source_id"] == source_id and edge["target_id"] == target_id
               and edge["relationship"] == relationship.value for edge in graph["edges"]):
        graph["edges"].append(KnowledgeEdge(source_id, target_id, relationship, status, notes).to_dict())


def _add_document_metadata_nodes(graph: dict, document: dict, document_id: str) -> None:
    """Index PDF metadata as candidates without treating it as verified identity."""
    document_node = next((node for node in graph["nodes"] if node["node_id"] == document_id), None)
    properties = {"sha256": document.get("sha256"), "origin": document.get("origin"),
                  "title": document.get("title"), "doi_candidate": document.get("doi"),
                  "authors_candidate": document.get("authors", [])}
    if document_node is None:
        document_node = KnowledgeNode(document_id, NodeType.DOCUMENT, document.get("filename", document_id), properties).to_dict()
        graph["nodes"].append(document_node)
    else:
        document_node["properties"].update(properties)
    doi = document.get("doi")
    paper_id = f"paper:doi:{doi.strip().lower()}" if isinstance(doi, str) and doi.strip() else f"paper:{document_id}"
    if not any(node["node_id"] == paper_id for node in graph["nodes"]):
        graph["nodes"].append(KnowledgeNode(paper_id, NodeType.PAPER,
            document.get("title") or document.get("filename") or "Imported paper",
            {"doi_candidate": doi, "metadata_status": "unverified_pdf_metadata"}).to_dict())
    _ensure_edge(graph, document_id, paper_id, Relationship.DOCUMENT_VERSION_OF, "proposed",
                 "Imported PDF metadata has not been independently verified.")
    for author in document.get("authors", []):
        if not isinstance(author, str) or not author.strip():
            continue
        author_name = author.strip()
        author_id = "author:" + hashlib.sha256(author_name.casefold().encode("utf-8")).hexdigest()
        if not any(node["node_id"] == author_id for node in graph["nodes"]):
            graph["nodes"].append(KnowledgeNode(author_id, NodeType.AUTHOR, author_name,
                {"metadata_status": "unverified_pdf_metadata"}).to_dict())
        _ensure_edge(graph, paper_id, author_id, Relationship.AUTHORED_BY, "proposed",
                     "Author name extracted from PDF metadata; verify against the source.")


def add_claim(
    output_dir: str | Path,
    document_id: str,
    page_number: int,
    passage_text: str,
    claim_text: str,
    section_name: str | None = None,
    evidence_category: str = EvidenceCategory.UNVERIFIED.value,
    extraction_method: str = "researcher_entered",
) -> dict:
    if not isinstance(page_number, int) or page_number < 1:
        raise ValueError("Page number must be a positive integer.")
    if evidence_category not in {item.value for item in EvidenceCategory}:
        raise ValueError("Unsupported evidence category.")
    if not isinstance(extraction_method, str) or not extraction_method.strip() or len(extraction_method) > 200:
        raise ValueError("Extraction method is required and must be 200 characters or fewer.")
    document = next((item for item in list_documents(output_dir) if item["document_id"] == document_id), None)
    if not document:
        raise ValueError("The source document is not in this local library.")
    if page_number > document.get("page_count", 0):
        raise ValueError("Page number is outside the extracted document.")
    if not isinstance(passage_text, str) or not passage_text.strip() or len(passage_text) > 20000:
        raise ValueError("Source passage must contain 1 to 20000 characters.")
    if not isinstance(claim_text, str) or not claim_text.strip() or len(claim_text) > 4000:
        raise ValueError("Claim must contain 1 to 4000 characters.")
    claim = ResearchClaim(
        original_text=claim_text.strip(),
        source=SourcePassage(document_id, page_number, passage_text.strip(), section_name or None),
        evidence_category=EvidenceCategory(evidence_category),
        verification_status=VerificationStatus.PROVISIONAL,
        extraction_method=extraction_method,
    )
    graph = load_graph(output_dir)
    _add_document_metadata_nodes(graph, document, document_id)
    graph["nodes"].append(KnowledgeNode(claim.claim_id, NodeType.CLAIM, claim.original_text,
                                          {"verification_status": claim.verification_status.value,
                                           "evidence_category": claim.evidence_category.value}).to_dict())
    _ensure_edge(graph, claim.claim_id, document_id, Relationship.DERIVED_FROM,
                 status="proposed", notes=f"Page {page_number}")
    graph["claims"].append(claim.to_dict())
    save_graph(output_dir, graph)
    return claim.to_dict()


def update_claim_review(output_dir: str | Path, claim_id: str, status: str, reviewer_note: str = "") -> dict:
    allowed = {item.value for item in VerificationStatus}
    if status not in allowed:
        raise ValueError("Unsupported verification status.")
    graph = load_graph(output_dir)
    claim = next((item for item in graph["claims"] if item["claim_id"] == claim_id), None)
    if not claim:
        raise FileNotFoundError("Claim not found.")
    claim["verification_status"] = status
    claim["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    claim["reviewer_note"] = reviewer_note.strip()[:2000]
    for node in graph["nodes"]:
        if node["node_id"] == claim_id:
            node["properties"]["verification_status"] = status
    for edge in graph["edges"]:
        if edge["source_id"] == claim_id and edge["relationship"] == Relationship.DERIVED_FROM.value:
            edge["status"] = "reviewed" if status in {"reviewed", "verified"} else "proposed"
            if reviewer_note.strip():
                edge["notes"] = f"{edge['notes']} · {reviewer_note.strip()[:500]}"
    save_graph(output_dir, graph)
    return claim


def add_relationship(output_dir: str | Path, source_id: str, target_id: str, relationship: str, notes: str = "") -> dict:
    if relationship not in {item.value for item in Relationship}:
        raise ValueError("Unsupported evidence relationship.")
    if not isinstance(notes, str) or not notes.strip() or len(notes) > 2000:
        raise ValueError("A relationship requires a context note of 1 to 2000 characters.")
    graph = load_graph(output_dir)
    ids = {node["node_id"] for node in graph["nodes"]}
    if source_id not in ids or target_id not in ids:
        raise ValueError("Both graph nodes must exist before linking them.")
    if source_id == target_id:
        raise ValueError("A node cannot be related to itself.")
    edge = KnowledgeEdge(source_id, target_id, Relationship(relationship), "proposed", notes.strip()).to_dict()
    graph["edges"].append(edge)
    save_graph(output_dir, graph)
    return edge


def review_relationship(output_dir: str | Path, edge_id: str, status: str, reviewer_note: str = "") -> dict:
    """Record a researcher's decision on a proposed evidence relationship."""
    if status not in {"reviewed", "rejected"}:
        raise ValueError("Relationship review status must be reviewed or rejected.")
    if not isinstance(reviewer_note, str) or not reviewer_note.strip() or len(reviewer_note) > 2000:
        raise ValueError("Relationship review requires a context note of 1 to 2000 characters.")
    graph = load_graph(output_dir)
    edge = next((item for item in graph["edges"] if item.get("edge_id") == edge_id), None)
    if edge is None or edge.get("relationship") == Relationship.DERIVED_FROM.value:
        raise FileNotFoundError("Proposed relationship not found.")
    edge["status"] = status
    edge["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    edge["reviewer_note"] = reviewer_note.strip()
    save_graph(output_dir, graph)
    return edge


def search_knowledge(output_dir: str | Path, query: str) -> list[dict]:
    """Search extracted page text and saved claims, returning source locations."""
    value = query.strip().casefold()
    if len(value) < 2:
        raise ValueError("Enter at least two characters to search the local library.")
    matches = []
    for document in list_documents(output_dir):
        for page in document.get("pages", []):
            text = page.get("text", "")
            index = text.casefold().find(value)
            if index >= 0:
                start, end = max(0, index - 100), min(len(text), index + len(value) + 180)
                matches.append({"type": "document_passage", "document_id": document["document_id"],
                                "filename": document.get("filename"), "page": page["page"],
                                "section_name": page.get("section_name"), "snippet": text[start:end]})
    graph = load_graph(output_dir)
    for claim in graph.get("claims", []):
        if value in claim.get("original_text", "").casefold():
            document = next((item for item in list_documents(output_dir)
                             if item["document_id"] == claim["source"]["document_id"]), {})
            matches.append({"type": "claim", "claim_id": claim["claim_id"], "filename": document.get("filename"),
                            "page": claim["source"]["page_number"], "snippet": claim["original_text"],
                            "verification_status": claim["verification_status"]})
    return matches

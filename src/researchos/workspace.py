"""Local browser-workspace records and controlled document intake."""

from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import uuid


VERSION_STATUS = [
    {"tracking": "v1", "release": "v0.2", "title": "Knowledge search foundation", "status": "complete"},
    {"tracking": "v2", "release": "v0.3", "title": "Evidence intelligence", "status": "complete"},
    {"tracking": "v3", "release": "v0.4", "title": "Methodology and study design", "status": "complete"},
    {"tracking": "v4", "release": "v0.5", "title": "Experiments and reproducibility", "status": "complete"},
    {"tracking": "v5", "release": "v0.6", "title": "Multi-agent runtime", "status": "complete"},
    {"tracking": "v6", "release": "v0.7", "title": "Scientific writing", "status": "complete"},
    {"tracking": "v7", "release": "v0.8", "title": "Plugins and collaboration", "status": "complete"},
    {"tracking": "v8", "release": "v0.9", "title": "Reliability and quality", "status": "complete"},
    {"tracking": "v9", "release": "v1.0", "title": "Integrated release", "status": "complete"},
]
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_version_note(tracking_version: str) -> str:
    if tracking_version not in {f"v{number}" for number in range(1, 10)}:
        raise ValueError("Unknown version note.")
    path = PROJECT_ROOT / f"{tracking_version}.md"
    if not path.is_file():
        raise FileNotFoundError("Version note is not installed in this workspace.")
    return path.read_text(encoding="utf-8")


def get_build_progress() -> dict:
    path = PROJECT_ROOT / "BUILD_PROGRESS.json"
    active = next((item for item in VERSION_STATUS if item["status"] == "in_progress"), VERSION_STATUS[-1])
    if not path.is_file():
        complete = active["status"] == "complete"
        return {"release": active["release"], "status": "complete" if complete else "in_progress",
                "current_task": "All tracked releases are complete." if complete else "Progress note not available",
                "completed": [], "next": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        complete = active["status"] == "complete"
        return {"release": active["release"], "status": "complete" if complete else "in_progress",
                "current_task": "All tracked releases are complete." if complete else "Could not read progress note",
                "completed": [], "next": []}


def _chat_path(root: Path, chat_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", chat_id):
        raise ValueError("Invalid chat identifier.")
    return root / "workspace" / "chats" / f"{chat_id}.json"


def list_chats(output_dir: str | Path) -> list[dict]:
    folder = Path(output_dir) / "workspace" / "chats"
    if not folder.exists():
        return []
    chats = []
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            chats.append({"id": data["id"], "title": data["title"], "updated_at": data["updated_at"]})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(chats, key=lambda item: item["updated_at"], reverse=True)


def get_chat(output_dir: str | Path, chat_id: str) -> dict:
    path = _chat_path(Path(output_dir), chat_id)
    if not path.exists():
        raise FileNotFoundError("Chat not found.")
    return json.loads(path.read_text(encoding="utf-8"))


def create_chat(output_dir: str | Path) -> dict:
    chat_id = str(uuid.uuid4())
    data = {"id": chat_id, "title": "New chat", "created_at": _now(), "updated_at": _now(), "messages": []}
    path = _chat_path(Path(output_dir), chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def append_chat_message(
    output_dir: str | Path, chat_id: str, role: str, content: str,
    output_style: str = "balanced", input_style: str = "chat", source: str = "local",
) -> dict:
    if role not in {"user", "assistant", "system"}:
        raise ValueError("Unsupported chat message role.")
    if not isinstance(content, str) or not content.strip() or len(content) > 12000:
        raise ValueError("Message must contain 1 to 12000 characters.")
    path = _chat_path(Path(output_dir), chat_id)
    if not path.exists():
        raise FileNotFoundError("Chat not found. Create a new chat first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    message = {"id": str(uuid.uuid4()), "role": role, "content": content.strip(), "created_at": _now()}
    if role == "assistant":
        message["source"] = source if source in {"local", "ai"} else "local"
    data["messages"].append(message)
    if role == "user" and data["title"] == "New chat":
        data["title"] = content.strip().splitlines()[0][:60]
    data["output_style"] = output_style if output_style in {"balanced", "concise", "detailed", "academic"} else "balanced"
    data["input_style"] = input_style if input_style in {"chat", "command", "note"} else "chat"
    data["updated_at"] = _now()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return message


def edit_chat_message(
    output_dir: str | Path, chat_id: str, message_id: str, content: str,
    output_style: str = "balanced", input_style: str = "chat",
    assistant_response: str | None = None, response_source: str = "local",
) -> dict:
    if not isinstance(content, str) or not content.strip() or len(content) > 12000:
        raise ValueError("Message must contain 1 to 12000 characters.")
    path = _chat_path(Path(output_dir), chat_id)
    if not path.exists():
        raise FileNotFoundError("Chat not found.")
    data = json.loads(path.read_text(encoding="utf-8"))
    index = next((i for i, item in enumerate(data["messages"]) if item["id"] == message_id and item["role"] == "user"), None)
    if index is None:
        raise FileNotFoundError("User message not found.")
    data["messages"] = data["messages"][:index]
    updated = {"id": message_id, "role": "user", "content": content.strip(), "created_at": _now()}
    data["messages"].append(updated)
    if index == 0:
        data["title"] = content.strip().splitlines()[0][:60]
    assistant = {"id": str(uuid.uuid4()), "role": "assistant",
                 "content": assistant_response if assistant_response is not None else workspace_reply(content, output_style),
                 "created_at": _now(), "source": response_source if response_source in {"local", "ai"} else "local"}
    data["messages"].append(assistant)
    data["output_style"] = output_style if output_style in {"balanced", "concise", "detailed", "academic"} else "balanced"
    data["input_style"] = input_style if input_style in {"chat", "command", "note"} else "chat"
    data["updated_at"] = _now()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"user_message": updated, "assistant_message": assistant, "chat": data}


def workspace_reply(message: str, output_style: str = "balanced") -> str:
    """Provide factual local-workspace guidance; this is not an LLM response."""
    lowered = message.strip().lower()
    active = next((item for item in VERSION_STATUS if item["status"] == "in_progress"), VERSION_STATUS[-1])
    active_status = active["status"] == "in_progress"
    if any(word in lowered for word in ("status", "update", "progress", "versions")):
        milestone = (f"Active build: {active['release']} — {active['title']}" if active_status else
                     f"Latest release: {active['release']} — {active['title']} is complete")
        if output_style == "concise":
            return f"{milestone}. Local workflows work without an AI provider; open Versions & progress for the roadmap."
        if output_style == "academic":
            return (f"ResearchOS {('is currently developing' if active_status else 'has completed')} {active['release']}, the {active['title']} milestone. "
                    "The v0.2 literature discovery workflow remains available. Optional AI actions require a configured provider, and no generated research analysis is being supplied in this chat. "
                    "The Versions & progress panel records completed and planned releases.")
        detail = "The v0.2 literature discovery workflow remains available. Local agent workflows work without credentials; the optional AI provider-backed planner appears only when a provider, model, and key are configured. Open the Versions panel to see all nine tracked releases."
        if output_style == "detailed":
            detail += " Evidence records retain page provenance and require researcher review. Current experiment work records datasets and execution provenance; code execution requires an explicitly configured isolated sandbox."
        return f"{milestone}. {detail}"
    if output_style == "concise":
        return "Your message is saved locally. AI research analysis is not enabled yet; use New research to create a literature brief."
    if output_style == "academic":
        return ("The message has been stored in the local conversation. ResearchOS is not connected to an AI model, so this response provides workspace guidance only. "
                "A literature discovery brief can be created using the New research action.")
    base = (f"Your message is saved in this local chat. ResearchOS {('is currently developing' if active_status else 'has completed')} {active['release']}, the {active['title']} milestone; "
            "an AI model is not connected, so this reply is workspace guidance rather than generated research analysis. "
            "You can still create a literature brief from a research topic and field using the New research action.")
    if output_style == "detailed":
        base += " PDF uploads are kept with SHA-256 provenance; extracted text and manually entered claims are local. Optional AI suggestions remain provisional, and Python experiments run only in the configured isolated Docker sandbox."
    return base


def store_pdf(output_dir: str | Path, filename: str, encoded_data: str) -> dict:
    """Store a bounded, user-selected PDF and honest extraction provenance."""
    clean_name = Path(filename.replace("\\", "/")).name
    if not clean_name.lower().endswith(".pdf"):
        raise ValueError("Only PDF documents are accepted in this workspace.")
    if not isinstance(encoded_data, str) or len(encoded_data) > 28_000_000:
        raise ValueError("PDF upload exceeds the 20 MB limit.")
    try:
        content = base64.b64decode(encoded_data, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("The uploaded file data is invalid.") from exc
    if not content.startswith(b"%PDF-"):
        raise ValueError("The uploaded file is not a valid PDF header.")
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("PDF upload exceeds the 20 MB limit.")
    digest = hashlib.sha256(content).hexdigest()
    folder = Path(output_dir) / "documents"
    folder.mkdir(parents=True, exist_ok=True)
    document_path = folder / f"{digest}.pdf"
    metadata_path = folder / f"{digest}.json"
    if not document_path.exists():
        document_path.write_bytes(content)
    previous = {}
    if metadata_path.exists():
        try:
            previous = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
    extraction_status = "not_attempted"
    extraction_method = None
    pages: list[dict] = []
    extracted = {"title": None, "authors": [], "doi": None, "sections": [], "page_count": 0,
                 "ocr_status": "not_attempted", "table_candidates": [], "tables": [], "tables_status": "not_extracted",
                 "tables_parse_status": "not_extracted",
                 "references": [], "references_status": "not_extracted"}
    try:
        import pypdf
        from researchos.knowledge.documents.pdf import extract_pdf
        extracted = extract_pdf(content)
        pages = extracted["pages"]
        extraction_method = f"pypdf:{pypdf.__version__}"
        extraction_status = extracted["extraction_status"]
    except ImportError:
        extraction_status = "extractor_unavailable"
    except Exception as exc:
        extraction_status = "extraction_failed"
        extraction_method = f"pypdf error: {type(exc).__name__}"
    imported_at = _now()
    import_event = {"filename": clean_name, "origin": "researcher_upload", "imported_at": imported_at}
    prior_imports = previous.get("imports", [])
    metadata = {
        "document_id": digest,
        "filename": previous.get("filename", clean_name),
        "filenames": sorted(set(previous.get("filenames", [previous.get("filename", clean_name)]) + [clean_name])),
        "sha256": digest,
        "origin": "researcher_upload",
        "imported_at": previous.get("imported_at", imported_at),
        "imports": prior_imports + [import_event],
        "extracted_at": _now() if extraction_method else None,
        "storage_path": str(document_path),
        "extraction_method": extraction_method,
        "extraction_status": extraction_status,
        "extraction_error": extraction_method if extraction_status == "extraction_failed" else None,
        "title": extracted.get("title"),
        "authors": extracted.get("authors", []),
        "doi": extracted.get("doi"),
        "pages": pages,
        "page_count": extracted.get("page_count", len(pages)),
        "sections": extracted.get("sections", []),
        "references": extracted.get("references", []),
        "table_candidates": extracted.get("table_candidates", []),
        "tables": extracted.get("tables", []),
        "ocr_status": extracted.get("ocr_status", "not_attempted"),
        "tables_status": extracted.get("tables_status", "not_implemented"),
        "tables_parse_status": extracted.get("tables_parse_status", "not_implemented"),
        "references_status": extracted.get("references_status", "not_extracted"),
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def list_documents(output_dir: str | Path) -> list[dict]:
    folder = Path(output_dir) / "documents"
    if not folder.exists():
        return []
    documents = []
    for path in folder.glob("*.json"):
        try:
            documents.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(documents, key=lambda item: item.get("imported_at", ""), reverse=True)

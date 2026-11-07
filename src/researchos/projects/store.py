"""Authenticated local project records with role checks and attributed revisions."""

from datetime import datetime, timezone
import base64
import json
from pathlib import Path
import re
import threading
import uuid

from researchos.security.auth import list_users


ROLES = {"owner", "editor", "reviewer", "viewer"}
RECORD_TYPES = {"paper", "claim", "study_design", "dataset", "experiment", "evaluation", "writing_draft", "research_run", "repository"}
_LOCK = threading.RLock()


def _serialized(method):
    def call(self, *args, **kwargs):
        with _LOCK:
            return method(self, *args, **kwargs)
    return call


def _now(): return datetime.now(timezone.utc).isoformat()


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor: return 0
    if not isinstance(cursor, str) or len(cursor) > 40: raise ValueError("Pagination cursor is invalid.")
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cursor): raise ValueError
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True).decode("ascii")
        offset = int(raw)
    except (ValueError, UnicodeDecodeError) as exc: raise ValueError("Pagination cursor is invalid.") from exc
    if offset < 0: raise ValueError("Pagination cursor is invalid.")
    return offset


def page_items(items: list, limit: int, cursor: str | None) -> dict:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Page size must be between 1 and 100.")
    offset = decode_cursor(cursor)
    selected = items[offset:offset + limit]
    next_offset = offset + len(selected)
    return {"items": selected, "next_cursor": encode_cursor(next_offset) if next_offset < len(items) else None,
            "total": len(items), "limit": limit}


class ProjectStore:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.folder = self.output_dir / "projects"

    @_serialized
    def create(self, payload: dict, actor: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) - {"title", "description"}:
            raise ValueError("Project request contains unsupported fields.")
        title, description = payload.get("title"), payload.get("description", "")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Project title is required and limited to 200 characters.")
        if not isinstance(description, str) or len(description) > 4000:
            raise ValueError("Project description must be text no longer than 4000 characters.")
        project_id = str(uuid.uuid4())
        created = _now()
        project = {"project_id": project_id, "title": title.strip(), "description": description.strip(),
            "owner": actor["username"], "members": {actor["username"]: "owner"}, "collections": [],
            "reviews": [], "revision": 1, "created_at": created, "updated_at": created,
            "events": [{"revision": 1, "action": "project_created", "actor": actor["username"], "at": created,
                        "snapshot": {"title": title.strip(), "description": description.strip()}}]}
        self._write(project)
        return project

    def list(self, actor: dict) -> list[dict]:
        if not self.folder.exists(): return []
        projects = []
        for path in self.folder.glob("*/project.json"):
            try:
                project = json.loads(path.read_text(encoding="utf-8"))
                if actor.get("admin") or actor.get("username") in project.get("members", {}):
                    projects.append(project)
            except (OSError, ValueError): continue
        return sorted(projects, key=lambda item: (item.get("updated_at", ""), item.get("project_id", "")), reverse=True)

    def get(self, project_id: str, actor: dict, minimum_role: str = "viewer") -> dict:
        project = self._load(project_id)
        self._authorize(project, actor, minimum_role)
        return project

    @_serialized
    def update(self, project_id: str, payload: dict, actor: dict) -> dict:
        project = self._load(project_id); self._authorize(project, actor, "editor")
        if not isinstance(payload, dict) or set(payload) - {"title", "description"} or not payload:
            raise ValueError("Project update must contain title or description only.")
        if "title" in payload:
            if not isinstance(payload["title"], str) or not payload["title"].strip() or len(payload["title"]) > 200:
                raise ValueError("Project title is required and limited to 200 characters.")
            project["title"] = payload["title"].strip()
        if "description" in payload:
            if not isinstance(payload["description"], str) or len(payload["description"]) > 4000:
                raise ValueError("Project description must be no longer than 4000 characters.")
            project["description"] = payload["description"].strip()
        self._event(project, actor, "project_updated", {key: project[key] for key in payload})
        self._write(project)
        return project

    @_serialized
    def add_member(self, project_id: str, payload: dict, actor: dict) -> dict:
        project = self._load(project_id); self._authorize(project, actor, "owner")
        if not isinstance(payload, dict) or set(payload) != {"username", "role"}:
            raise ValueError("Member request must contain username and role.")
        username, role = payload["username"], payload["role"]
        users = {item.get("username") for item in list_users(self.output_dir) if item.get("disabled") is not True}
        if not isinstance(username, str) or username not in users:
            raise ValueError("Project member must be an active local ResearchOS user.")
        if role not in {"editor", "reviewer", "viewer"}:
            raise ValueError("Project member role must be editor, reviewer, or viewer.")
        if username == project["owner"]:
            raise ValueError("The project owner's role cannot be changed or reassigned.")
        previous = project["members"].get(username)
        project["members"][username] = role
        self._event(project, actor, "member_added" if previous is None else "member_role_changed",
                    {"username": username, "role": role})
        self._write(project)
        return project

    @_serialized
    def remove_member(self, project_id: str, username: str, actor: dict) -> dict:
        project = self._load(project_id); self._authorize(project, actor, "owner")
        if username == project["owner"]: raise ValueError("Project owner cannot be removed.")
        if username not in project["members"]: raise FileNotFoundError("Project member not found.")
        role = project["members"].pop(username)
        self._event(project, actor, "member_removed", {"username": username, "previous_role": role})
        self._write(project)
        return project

    @_serialized
    def create_collection(self, project_id: str, payload: dict, actor: dict) -> dict:
        project = self._load(project_id); self._authorize(project, actor, "editor")
        if not isinstance(payload, dict) or set(payload) - {"title", "description", "records"}:
            raise ValueError("Collection contains unsupported fields.")
        title, description, records = payload.get("title"), payload.get("description", ""), payload.get("records", [])
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Collection title is required and limited to 200 characters.")
        if not isinstance(description, str) or len(description) > 2000:
            raise ValueError("Collection description is limited to 2000 characters.")
        clean_records = _validate_record_refs(records)
        collection = {"collection_id": str(uuid.uuid4()), "title": title.strip(), "description": description.strip(),
                      "records": clean_records, "created_by": actor["username"], "created_at": _now(), "updated_at": _now()}
        project["collections"].append(collection)
        self._event(project, actor, "collection_created", json.loads(json.dumps(collection)))
        self._write(project)
        return project

    @_serialized
    def update_collection(self, project_id: str, collection_id: str, payload: dict, actor: dict) -> dict:
        project = self._load(project_id); self._authorize(project, actor, "editor")
        collection = self._collection(project, collection_id)
        if not isinstance(payload, dict) or set(payload) - {"title", "description", "add_records", "remove_records"} or not payload:
            raise ValueError("Collection update contains unsupported fields.")
        if "title" in payload:
            if not isinstance(payload["title"], str) or not payload["title"].strip() or len(payload["title"]) > 200:
                raise ValueError("Collection title is required and limited to 200 characters.")
            collection["title"] = payload["title"].strip()
        if "description" in payload:
            if not isinstance(payload["description"], str) or len(payload["description"]) > 2000:
                raise ValueError("Collection description is limited to 2000 characters.")
            collection["description"] = payload["description"].strip()
        if "remove_records" in payload:
            remove = _validate_record_refs(payload["remove_records"])
            remove_keys = {(item["record_type"], item["record_id"]) for item in remove}
            collection["records"] = [item for item in collection["records"] if (item["record_type"], item["record_id"]) not in remove_keys]
        if "add_records" in payload:
            additions = _validate_record_refs(payload["add_records"])
            existing = {(item["record_type"], item["record_id"]) for item in collection["records"]}
            if existing.intersection((item["record_type"], item["record_id"]) for item in additions):
                raise ValueError("Collection already contains one or more selected records.")
            collection["records"].extend(additions)
        collection["updated_at"] = _now()
        self._event(project, actor, "collection_updated", json.loads(json.dumps(collection)))
        self._write(project)
        return project

    @_serialized
    def add_review(self, project_id: str, payload: dict, actor: dict) -> dict:
        project = self._load(project_id); self._authorize(project, actor, "reviewer")
        if not isinstance(payload, dict) or set(payload) - {"collection_id", "comment", "decision"}:
            raise ValueError("Review record contains unsupported fields.")
        comment, decision = payload.get("comment"), payload.get("decision", "comment")
        if not isinstance(comment, str) or not comment.strip() or len(comment) > 4000:
            raise ValueError("Review comment is required and limited to 4000 characters.")
        if decision not in {"comment", "approve", "request_revision", "reject"}:
            raise ValueError("Unsupported review decision.")
        collection_id = payload.get("collection_id")
        if collection_id is not None: self._collection(project, collection_id)
        review = {"review_id": str(uuid.uuid4()), "collection_id": collection_id,
                  "comment": comment.strip(), "decision": decision, "reviewer": actor["username"], "created_at": _now()}
        project["reviews"].append(review)
        self._event(project, actor, "review_recorded", json.loads(json.dumps(review)))
        self._write(project)
        return project

    def revisions(self, project_id: str, actor: dict) -> list[dict]:
        project = self.get(project_id, actor, "viewer")
        return list(reversed(project["events"]))

    def _load(self, project_id: str) -> dict:
        if not isinstance(project_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", project_id):
            raise ValueError("Invalid project identifier.")
        path = self.folder / project_id / "project.json"
        if not path.is_file(): raise FileNotFoundError("Project not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _collection(project: dict, collection_id: str) -> dict:
        if not isinstance(collection_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", collection_id):
            raise ValueError("Invalid collection identifier.")
        collection = next((item for item in project["collections"] if item["collection_id"] == collection_id), None)
        if collection is None: raise FileNotFoundError("Collection not found.")
        return collection

    @staticmethod
    def _authorize(project: dict, actor: dict, required: str) -> None:
        if actor.get("admin"): return
        role = project.get("members", {}).get(actor.get("username"))
        hierarchy = {"viewer": 0, "reviewer": 1, "editor": 2, "owner": 3}
        if role not in hierarchy or hierarchy[role] < hierarchy[required]:
            raise PermissionError("This project role does not allow the requested action.")

    @staticmethod
    def _event(project: dict, actor: dict, action: str, snapshot: dict) -> None:
        project["revision"] += 1
        project["updated_at"] = _now()
        project["events"].append({"revision": project["revision"], "action": action,
                                  "actor": actor["username"], "at": project["updated_at"], "snapshot": snapshot})

    def _write(self, project: dict) -> None:
        path = self.folder / project["project_id"] / "project.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)


def _validate_record_refs(records: object) -> list[dict]:
    if not isinstance(records, list) or len(records) > 500:
        raise ValueError("A collection accepts up to 500 record references.")
    clean, seen = [], set()
    for item in records:
        if not isinstance(item, dict) or set(item) != {"record_type", "record_id"}:
            raise ValueError("Each collection reference requires record_type and record_id.")
        kind, record_id = item["record_type"], item["record_id"]
        if kind not in RECORD_TYPES or not isinstance(record_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:/%-]{1,200}", record_id):
            raise ValueError("Collection record reference has an invalid type or identifier.")
        key = (kind, record_id)
        if key in seen: raise ValueError("Collection record references must be unique.")
        seen.add(key); clean.append({"record_type": kind, "record_id": record_id})
    return clean

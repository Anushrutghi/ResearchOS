"""Small typed HTTP client for the authenticated ResearchOS API."""

from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ResearchOSError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ResearchOSClient:
    """Client for projects, collections, revisions, and declared plugins."""

    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an HTTP(S) URL.")
        if not isinstance(token, str) or not token or len(token) > 256:
            raise ValueError("A valid bearer token is required.")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 120:
            raise ValueError("timeout must be between 0 and 120 seconds.")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(self.base_url + path, data=body, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/json",
            "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read(2_000_001).decode("utf-8"))
        except HTTPError as exc:
            try:
                data = json.loads(exc.read(65_536).decode("utf-8"))
                message = data.get("error", "Request failed") if isinstance(data, dict) else "Request failed"
            except (ValueError, UnicodeDecodeError):
                message = "Request failed"
            finally:
                exc.close()
            raise ResearchOSError(str(message), exc.code) from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise ResearchOSError("ResearchOS request failed or returned invalid JSON.") from exc
        if not isinstance(result, dict):
            raise ResearchOSError("ResearchOS returned an invalid response.")
        return result

    @staticmethod
    def _project_id(project_id: str) -> str:
        if not isinstance(project_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", project_id):
            raise ValueError("project_id must be a UUID.")
        return project_id

    def list_projects(self, limit: int = 50, cursor: str | None = None) -> dict:
        query = {"limit": limit}
        if cursor:
            query["cursor"] = cursor
        return self._request("GET", "/api/projects?" + urlencode(query))

    def iter_projects(self, page_size: int = 50):
        cursor = None
        while True:
            page = self.list_projects(page_size, cursor)
            yield from page["items"]
            cursor = page.get("next_cursor")
            if not cursor:
                break

    def create_project(self, title: str, description: str = "") -> dict:
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Project title is required and limited to 200 characters.")
        if not isinstance(description, str) or len(description) > 4000:
            raise ValueError("Project description is limited to 4000 characters.")
        return self._request("POST", "/api/projects", {"title": title, "description": description})

    def get_project(self, project_id: str) -> dict:
        project_id = self._project_id(project_id)
        return self._request("GET", f"/api/projects/{project_id}")

    def project_lifecycle(self, project_id: str) -> dict:
        project_id = self._project_id(project_id)
        return self._request("GET", f"/api/projects/{project_id}/lifecycle")

    def update_project(self, project_id: str, **changes) -> dict:
        project_id = self._project_id(project_id)
        if not changes or set(changes) - {"title", "description"}:
            raise ValueError("Only title and description can be changed.")
        return self._request("PUT", f"/api/projects/{project_id}", changes)

    def add_member(self, project_id: str, username: str, role: str) -> dict:
        project_id = self._project_id(project_id)
        if role not in {"editor", "reviewer", "viewer"}:
            raise ValueError("role must be editor, reviewer, or viewer.")
        return self._request("POST", f"/api/projects/{project_id}/members", {"username": username, "role": role})

    def remove_member(self, project_id: str, username: str) -> dict:
        project_id = self._project_id(project_id)
        if not isinstance(username, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{1,63}", username):
            raise ValueError("username is invalid.")
        return self._request("DELETE", f"/api/projects/{project_id}/members/{username}")

    def create_collection(self, project_id: str, title: str, description: str = "", records: list | None = None) -> dict:
        project_id = self._project_id(project_id)
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Collection title is required and limited to 200 characters.")
        if not isinstance(description, str) or len(description) > 2000:
            raise ValueError("Collection description is limited to 2000 characters.")
        if records is not None and not isinstance(records, list):
            raise ValueError("records must be a list.")
        return self._request("POST", f"/api/projects/{project_id}/collections", {
            "title": title, "description": description, "records": records or []})

    def update_collection(self, project_id: str, collection_id: str, **changes) -> dict:
        project_id = self._project_id(project_id)
        if not isinstance(collection_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", collection_id):
            raise ValueError("collection_id must be a UUID.")
        if not changes or set(changes) - {"title", "description", "add_records", "remove_records"}:
            raise ValueError("Collection update contains unsupported fields.")
        return self._request("PUT", f"/api/projects/{project_id}/collections/{collection_id}", changes)

    def add_review(self, project_id: str, comment: str, decision: str = "comment", collection_id: str | None = None) -> dict:
        project_id = self._project_id(project_id)
        if not isinstance(comment, str) or not comment.strip() or len(comment) > 4000:
            raise ValueError("Review comment is required and limited to 4000 characters.")
        if decision not in {"comment", "approve", "request_revision", "reject"}:
            raise ValueError("Review decision is invalid.")
        payload = {"comment": comment, "decision": decision}
        if collection_id is not None:
            if not re.fullmatch(r"[0-9a-f-]{36}", collection_id):
                raise ValueError("collection_id must be a UUID.")
            payload["collection_id"] = collection_id
        return self._request("POST", f"/api/projects/{project_id}/reviews", payload)

    def revisions(self, project_id: str, limit: int = 50, cursor: str | None = None) -> dict:
        project_id = self._project_id(project_id)
        query = {"limit": limit}
        if cursor:
            query["cursor"] = cursor
        return self._request("GET", f"/api/projects/{project_id}/revisions?" + urlencode(query))

    def list_plugins(self) -> dict:
        return self._request("GET", "/api/plugins")

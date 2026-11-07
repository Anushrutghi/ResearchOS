"""High-entropy local API tokens stored only as SHA-256 hashes."""

from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import threading
import uuid


_LOCK = threading.RLock()
_USERNAME = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")


def _path(output_dir):
    return Path(output_dir) / "auth" / "users.json"


def list_users(output_dir) -> list[dict]:
    path = _path(output_dir)
    if not path.exists(): return []
    value = json.loads(path.read_text(encoding="utf-8"))
    return value.get("users", []) if isinstance(value, dict) else []


def create_user(output_dir, username: str, display_name: str, admin: bool = False) -> tuple[dict, str]:
    if not isinstance(username, str) or not _USERNAME.fullmatch(username):
        raise ValueError("Username must be 2 to 64 lowercase letters, digits, dots, underscores, or hyphens and start with a letter.")
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 120:
        raise ValueError("Display name is required and limited to 120 characters.")
    if not isinstance(admin, bool): raise ValueError("Admin flag must be boolean.")
    with _LOCK:
        path = _path(output_dir)
        if path.exists():
            try: data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc: raise RuntimeError("User store is malformed; refusing to overwrite it.") from exc
        else:
            data = {"schema_version": 1, "users": []}
        users = data.get("users") if isinstance(data, dict) else None
        if not isinstance(users, list): raise RuntimeError("User store schema is invalid.")
        if any(item.get("username") == username for item in users):
            raise ValueError("Username already exists.")
        token = secrets.token_urlsafe(32)
        first_user = not users
        user = {"user_id": str(uuid.uuid4()), "username": username,
                "display_name": display_name.strip(), "admin": bool(admin or first_user),
                "token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
                "created_at": datetime.now(timezone.utc).isoformat(), "disabled": False}
        users.append(user)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(path)
        return {key: value for key, value in user.items() if key != "token_sha256"}, token


def authenticate(output_dir, token: str | None) -> dict | None:
    if not isinstance(token, str) or not token or len(token) > 256:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    try: users = list_users(output_dir)
    except (OSError, ValueError): return None
    for user in users:
        expected = user.get("token_sha256", "")
        if user.get("disabled") is not True and isinstance(expected, str) and hmac.compare_digest(expected, token_hash):
            return {key: user.get(key) for key in ("user_id", "username", "display_name", "admin")}
    return None


def revoke_user(output_dir, username: str) -> dict:
    with _LOCK:
        path = _path(output_dir)
        users = list_users(output_dir)
        user = next((item for item in users if item.get("username") == username), None)
        if not user: raise FileNotFoundError("User not found.")
        user["disabled"] = True
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps({"schema_version": 1, "users": users}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(path)
        return {key: value for key, value in user.items() if key != "token_sha256"}

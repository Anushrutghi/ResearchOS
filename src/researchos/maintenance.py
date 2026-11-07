"""Integrity-checked local backups and human-readable scientific QA checks."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile


MANIFEST_NAME = ".researchos-backup-manifest.json"
MAX_BACKUP_BYTES = 5 * 1024 * 1024 * 1024
MAX_BACKUP_FILES = 250_000


def create_backup(output_dir: str | Path, destination: str | Path, max_bytes: int = MAX_BACKUP_BYTES) -> dict:
    root = Path(output_dir).resolve()
    target = Path(destination).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("ResearchOS output directory does not exist.")
    if _within(target, root):
        raise ValueError("Backup destination must be outside the ResearchOS output directory.")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= MAX_BACKUP_BYTES:
        raise ValueError("Backup size limit is invalid.")
    files = _regular_files(root)
    if len(files) > MAX_BACKUP_FILES:
        raise ValueError("Backup contains too many files.")
    entries, total = [], 0
    for source in files:
        relative = source.relative_to(root).as_posix()
        if relative == MANIFEST_NAME:
            raise ValueError(f"Output directory contains the reserved backup path {MANIFEST_NAME}.")
        size = source.stat().st_size
        total += size
        if total > max_bytes:
            raise ValueError("Backup exceeds its configured uncompressed size limit.")
        entries.append({"path": relative, "size": size, "sha256": _hash_file(source)})
    manifest = {"format": "researchos-backup", "schema_version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(), "file_count": len(entries),
                "total_bytes": total, "files": entries}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".researchos-backup-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for source, entry in zip(files, entries):
                archive.add(source, arcname="data/" + entry["path"], recursive=False)
            raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            info = tarfile.TarInfo(MANIFEST_NAME)
            info.size = len(raw); info.mode = 0o600; info.mtime = 0
            import io
            archive.addfile(info, io.BytesIO(raw))
        if temporary.stat().st_size > max_bytes:
            raise ValueError("Compressed backup exceeds its configured size limit.")
        verify_backup(temporary, max_bytes)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {"path": str(target), "file_count": len(entries), "total_bytes": total,
            "archive_bytes": target.stat().st_size, "created_at": manifest["created_at"]}


def verify_backup(archive_path: str | Path, max_bytes: int = MAX_BACKUP_BYTES) -> dict:
    path = Path(archive_path)
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("Backup archive is missing or exceeds its size limit.")
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > MAX_BACKUP_FILES + 1:
                raise ValueError("Backup contains too many entries.")
            by_name = {}
            for member in members:
                if member.name in by_name or not member.isfile():
                    raise ValueError("Backup archive contains duplicate or non-regular entries.")
                by_name[member.name] = member
            manifest_member = by_name.get(MANIFEST_NAME)
            if manifest_member is None or manifest_member.size > 32 * 1024 * 1024:
                raise ValueError("Backup manifest is missing or too large.")
            manifest_file = archive.extractfile(manifest_member)
            manifest = json.loads(manifest_file.read(32 * 1024 * 1024 + 1).decode("utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("Backup manifest must be a JSON object.")
            entries = manifest.get("files")
            if manifest.get("format") != "researchos-backup" or manifest.get("schema_version") != 1 or not isinstance(entries, list) or len(entries) > MAX_BACKUP_FILES:
                raise ValueError("Backup manifest format is unsupported.")
            expected_names = {MANIFEST_NAME}
            total = 0; seen = set()
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
                    raise ValueError("Backup manifest file record is malformed.")
                relative = _safe_relative(entry["path"])
                if relative in seen or not isinstance(entry["size"], int) or isinstance(entry["size"], bool) or entry["size"] < 0:
                    raise ValueError("Backup manifest contains duplicate or invalid file records.")
                if not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64 or any(char not in "0123456789abcdef" for char in entry["sha256"]):
                    raise ValueError("Backup manifest contains an invalid digest.")
                seen.add(relative); total += entry["size"]
                if total > max_bytes:
                    raise ValueError("Backup exceeds its uncompressed size limit.")
                name = "data/" + relative
                member = by_name.get(name)
                if member is None or member.size != entry["size"]:
                    raise ValueError(f"Backup content is missing or has the wrong size: {relative}")
                source = archive.extractfile(member)
                digest = hashlib.sha256()
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                if digest.hexdigest() != entry["sha256"]:
                    raise ValueError(f"Backup content failed SHA-256 verification: {relative}")
                expected_names.add(name)
            if set(by_name) != expected_names or manifest.get("file_count") != len(entries) or manifest.get("total_bytes") != total:
                raise ValueError("Backup archive and manifest entries do not match.")
            return {"valid": True, "file_count": len(entries), "total_bytes": total,
                    "created_at": manifest.get("created_at"), "archive_bytes": path.stat().st_size}
    except (tarfile.TarError, OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, KeyError) as exc:
        raise ValueError("Backup archive is corrupt or unreadable.") from exc


def restore_backup(archive_path: str | Path, destination: str | Path,
                   max_bytes: int = MAX_BACKUP_BYTES) -> dict:
    report = verify_backup(archive_path, max_bytes)
    target = Path(destination).expanduser().resolve()
    if target.exists():
        raise ValueError("Restore destination must not already exist; choose a new directory.")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".researchos-restore-", dir=target.parent))
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            manifest_member = next(member for member in archive.getmembers() if member.name == MANIFEST_NAME)
            manifest = json.loads(archive.extractfile(manifest_member).read().decode("utf-8"))
            for entry in manifest["files"]:
                relative = _safe_relative(entry["path"])
                member = archive.getmember("data/" + relative)
                output = stage.joinpath(*PurePosixPath(relative).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                with output.open("xb") as sink:
                    shutil.copyfileobj(source, sink, 1024 * 1024)
                output.chmod(0o600 if relative == "auth/users.json" else 0o644)
        stage.replace(target)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {**report, "destination": str(target)}


def scientific_quality_report(output_dir: str | Path) -> dict:
    """Report traceability and integrity checks without reducing quality to a score."""
    root = Path(output_dir)
    checks = []
    def check(name, status, detail, count=None):
        item = {"check": name, "status": status, "detail": detail}
        if count is not None: item["records_checked"] = count
        checks.append(item)
    json_paths = []
    for relative in ("workspace/chats", "documents", "agent_workflows", "methodology/designs", "experiments",
                     "experiments/evaluations", "writing/documents", "projects", "plugins",
                     "datasets/versions", "datasets/imports"):
        folder = root / relative
        if folder.exists():
            json_paths.extend(folder.rglob("*.json"))
    json_paths.extend(root.glob("*/run.json"))
    json_paths = sorted(set(json_paths))
    invalid = []
    for path in json_paths[:250_000]:
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("JSON path is not a regular file.")
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            invalid.append(path.relative_to(root).as_posix())
    check("local_json_records", "fail" if invalid else "warn" if len(json_paths) > 250_000 else "pass",
          f"{len(invalid)} malformed or unreadable JSON records." if invalid else
          f"All {min(len(json_paths), 250_000)} sampled local JSON records parse." + (" Record limit reached; remaining files were not checked." if len(json_paths) > 250_000 else ""),
          min(len(json_paths), 250_000))
    objects = root / "datasets" / "objects"
    dataset_versions = root / "datasets" / "versions"
    checked = missing = corrupt = 0
    if dataset_versions.exists():
        for record_path in list(dataset_versions.glob("*.json"))[:100_000]:
            checked += 1
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                digest, suffix = record["dataset_id"], record["suffix"]
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or suffix not in {".csv", ".json"}:
                    corrupt += 1; continue
                obj = objects / f"{digest}{suffix}"
                if not obj.is_file(): missing += 1
                elif _hash_file(obj) != digest: corrupt += 1
            except (OSError, ValueError, KeyError, TypeError): corrupt += 1
    check("dataset_content_hashes", "fail" if corrupt else "warn" if missing else "pass",
          f"Verified {checked} dataset records; {corrupt} hash/metadata failures and {missing} missing objects.", checked)
    document_records = []
    for metadata_path in (root / "documents").glob("*.json") if (root / "documents").exists() else []:
        try:
            document_records.append(json.loads(metadata_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    doc_corrupt = doc_missing = 0
    for item in document_records:
        if not isinstance(item, dict):
            doc_corrupt += 1; continue
        document_id = item.get("document_id")
        if not isinstance(document_id, str) or not re.fullmatch(r"[0-9a-f]{64}", document_id):
            doc_corrupt += 1; continue
        path = root / "documents" / f"{document_id}.pdf"
        if not path.is_file(): doc_missing += 1
        else:
            try:
                if _hash_file(path) != item.get("sha256"): doc_corrupt += 1
            except (OSError, ValueError): doc_corrupt += 1
    check("source_document_hashes", "fail" if doc_corrupt else "warn" if doc_missing else "pass",
          f"Checked {len(document_records)} imported PDFs; {doc_corrupt} hash failures and {doc_missing} missing files.", len(document_records))
    experiment_records = sorted((root / "experiments").glob("*/record.json")) if (root / "experiments").exists() else []
    source_failures = artifact_failures = 0
    for record_path in experiment_records[:100_000]:
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            source_file = record.get("source_file")
            if source_file != "source.py" or _hash_file(record_path.parent / source_file) != record.get("source_sha256"):
                source_failures += 1
            for artifact in record.get("artifacts", []):
                filename = artifact.get("filename")
                if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", filename):
                    artifact_failures += 1; continue
                path = record_path.parent / "artifacts" / filename
                if not path.is_file() or _hash_file(path) != artifact.get("sha256"):
                    artifact_failures += 1
        except (OSError, ValueError, TypeError, AttributeError):
            source_failures += 1
    check("experiment_source_hashes", "fail" if source_failures else "pass",
          f"Checked {min(len(experiment_records), 100_000)} experiment source records; {source_failures} source-integrity failures.",
          min(len(experiment_records), 100_000))
    check("experiment_artifact_hashes", "fail" if artifact_failures else "pass",
          f"Found {artifact_failures} missing, unsafe, or hash-mismatched artifacts.")
    graph_path = root / "knowledge" / "evidence_graph.json"
    claim_count = unreviewed = 0
    if graph_path.is_file():
        try:
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            claims = graph.get("claims", []) if isinstance(graph, dict) else None
            if not isinstance(claims, list) or any(not isinstance(item, dict) for item in claims):
                raise ValueError("Evidence claims are malformed.")
            claim_count = len(claims); unreviewed = sum(item.get("verification_status") != "reviewed" for item in claims)
        except (OSError, ValueError):
            check("claim_review_state", "fail", "Evidence graph is malformed.")
    check("claim_review_state", "warn" if unreviewed else "pass",
          f"{unreviewed} of {claim_count} claims are not researcher-reviewed; provisional claims are not treated as verified.", claim_count)
    check("interpretation_boundary", "info",
          "This report checks local record integrity and review state. It does not assess truth, novelty, study validity, statistical significance, or publication quality.")
    status = "fail" if any(item["status"] == "fail" for item in checks) else "review" if any(item["status"] == "warn" for item in checks) else "pass"
    return {"schema_version": 1, "status": status, "generated_at": datetime.now(timezone.utc).isoformat(),
            "checks": checks}


def _regular_files(root: Path) -> list[Path]:
    files = []
    for current, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if not (Path(current) / name).is_symlink())
        for name in sorted(names):
            path = Path(current) / name
            if path.is_symlink():
                continue
            if path.is_file(): files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Backup contains an unsafe relative path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != value:
        raise ValueError("Backup contains an unsafe relative path.")
    return value


def _hash_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Integrity checks accept regular files only.")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024): digest.update(chunk)
    return digest.hexdigest()


def _within(path: Path, parent: Path) -> bool:
    try: path.relative_to(parent); return True
    except ValueError: return False

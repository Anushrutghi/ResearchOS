"""Content-addressed local dataset intake for CSV and JSON row collections."""

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import uuid


MAX_DATASET_BYTES = 50 * 1024 * 1024
MAX_DATASET_ROWS = 1_000_000
MAX_DATASET_CELLS = 10_000_000
MAX_PROFILE_ROWS = 10_000
MAX_PROFILE_CELLS = 2_000_000
_ALLOWED_SUFFIXES = {".csv", ".json"}


def list_datasets(output_dir: str | Path) -> list[dict]:
    folder = Path(output_dir) / "datasets" / "versions"
    if not folder.exists():
        return []
    records = []
    for path in folder.glob("*.json"):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)


def get_dataset(output_dir: str | Path, dataset_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", dataset_id):
        raise ValueError("Invalid dataset identifier.")
    path = Path(output_dir) / "datasets" / "versions" / f"{dataset_id}.json"
    if not path.is_file():
        raise FileNotFoundError("Dataset not found.")
    return json.loads(path.read_text(encoding="utf-8"))


def import_dataset(output_dir: str | Path, filename: str, content: bytes) -> dict:
    """Validate and preserve a CSV/JSON upload unchanged under its SHA-256 digest."""
    safe_name = Path(filename.replace("\\", "/")).name if isinstance(filename, str) else ""
    suffix = Path(safe_name).suffix.casefold()
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError("Only CSV and JSON datasets are supported in this milestone.")
    if not isinstance(content, bytes) or not content or len(content) > MAX_DATASET_BYTES:
        raise ValueError("Dataset must contain 1 byte to 50 MiB.")
    summary = _validate_csv(content) if suffix == ".csv" else _validate_json(content)
    return _store_dataset(output_dir, safe_name, content, suffix, summary, [], None)


def record_dataset_transformation(output_dir: str | Path, parent_ids: list[str], filename: str,
                                 content: bytes, description: str) -> dict:
    """Record caller-produced transformed bytes and explicit lineage; transformation code is not run here."""
    if not isinstance(parent_ids, list) or not parent_ids or len(parent_ids) > 20:
        raise ValueError("A transformation must reference 1 to 20 parent datasets.")
    parents = [get_dataset(output_dir, parent_id) for parent_id in dict.fromkeys(parent_ids)]
    if len(parents) != len(set(parent_ids)):
        raise ValueError("Parent dataset identifiers must be unique.")
    if not isinstance(description, str) or not description.strip() or len(description) > 2000:
        raise ValueError("Transformation description is required and limited to 2000 characters.")
    safe_name = Path(filename.replace("\\", "/")).name if isinstance(filename, str) else ""
    suffix = Path(safe_name).suffix.casefold()
    if suffix not in _ALLOWED_SUFFIXES or not isinstance(content, bytes) or not content or len(content) > MAX_DATASET_BYTES:
        raise ValueError("Transformed output must be a nonempty CSV or JSON file under 50 MiB.")
    summary = _validate_csv(content) if suffix == ".csv" else _validate_json(content)
    return _store_dataset(output_dir, safe_name, content, suffix, summary,
        [parent["dataset_id"] for parent in parents], description.strip())


def dataset_path(output_dir: str | Path, dataset_id: str) -> Path:
    record = get_dataset(output_dir, dataset_id)
    path = Path(output_dir) / "datasets" / "objects" / f"{dataset_id}{record['suffix']}"
    if not path.is_file() or _digest(path.read_bytes()) != dataset_id:
        raise RuntimeError("Stored dataset content is missing or does not match its recorded digest.")
    return path


def _store_dataset(output_dir: str | Path, filename: str, content: bytes, suffix: str,
                   summary: dict, parents: list[str], description: str | None) -> dict:
    digest = _digest(content)
    root = Path(output_dir) / "datasets"
    objects, versions, imports = root / "objects", root / "versions", root / "imports"
    for folder in (objects, versions, imports):
        folder.mkdir(parents=True, exist_ok=True)
    object_path = objects / f"{digest}{suffix}"
    if object_path.exists():
        if _digest(object_path.read_bytes()) != digest:
            raise RuntimeError("Content-addressed dataset object failed its integrity check.")
    else:
        object_path.write_bytes(content)
        object_path.chmod(0o444)
    version_path = versions / f"{digest}.json"
    if version_path.exists():
        record = json.loads(version_path.read_text(encoding="utf-8"))
        if record.get("suffix") != suffix:
            raise ValueError("Identical dataset bytes cannot be registered under conflicting file formats.")
    else:
        record = {"dataset_id": digest, "sha256": digest, "filename": filename, "suffix": suffix,
            "byte_size": len(content), "format": suffix[1:], "summary": summary, "parent_dataset_ids": parents,
            "transformation_description": description, "created_at": datetime.now(timezone.utc).isoformat()}
        version_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    event = {"import_id": str(uuid.uuid4()), "dataset_id": digest, "filename": filename,
        "imported_at": datetime.now(timezone.utc).isoformat(), "event_type": "transformation_output" if parents else "import"}
    (imports / f"{event['import_id']}.json").write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**record, "import_event": event}


def _validate_csv(content: bytes) -> dict:
    try:
        text = content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader)
        if not header or len(header) > 1000 or any(not item.strip() or len(item) > 200 for item in header) or len(set(header)) != len(header):
            raise ValueError("CSV header fields must be nonempty, unique, at most 200 characters, and at most 1,000 columns.")
        profiles = {name: _new_profile() for name in header}
        profile_limit = min(MAX_PROFILE_ROWS, max(1, MAX_PROFILE_CELLS // len(header)))
        rows = 0
        for row in reader:
            rows += 1
            if rows > MAX_DATASET_ROWS:
                raise ValueError("CSV exceeds the 1,000,000 row limit.")
            if rows * len(header) > MAX_DATASET_CELLS:
                raise ValueError("Dataset exceeds the 10,000,000 cell limit.")
            if len(row) != len(header):
                raise ValueError(f"CSV row {rows + 1} has {len(row)} fields; expected {len(header)}.")
            if rows <= profile_limit:
                for name, value in zip(header, row):
                    _add_profile_value(profiles[name], value if value != "" else None, csv_mode=True)
    except (UnicodeDecodeError, csv.Error, StopIteration, OverflowError) as exc:
        raise ValueError("CSV must be valid UTF-8 with a header row and consistent fields.") from exc
    profiled_rows = min(rows, profile_limit)
    return {"row_count": rows, "columns": header, "profile": _finish_profiles(profiles),
            "profiled_row_count": profiled_rows, "profile_complete": rows <= profile_limit,
            "profile_method": "first_rows_heuristic"}


def _validate_json(content: bytes) -> dict:
    try:
        text = content.decode("utf-8")
        columns = []
        seen_columns = set()
        row_count = 0
        for row in _iter_json_array(text):
            if not isinstance(row, dict):
                raise ValueError("JSON dataset must be a list of object rows.")
            row_count += 1
            if row_count > MAX_DATASET_ROWS:
                raise ValueError("JSON dataset exceeds the 1,000,000 row limit.")
            for key, value in row.items():
                if len(key) > 200:
                    raise ValueError("JSON dataset columns must be text fields of at most 200 characters, with at most 1,000 columns.")
                if key not in seen_columns:
                    seen_columns.add(key)
                    columns.append(key)
                    if len(columns) > 1000:
                        raise ValueError("JSON dataset columns must be text fields of at most 200 characters, with at most 1,000 columns.")
                _validate_json_value(value)
            if row_count * max(1, len(columns)) > MAX_DATASET_CELLS:
                raise ValueError("Dataset exceeds the 10,000,000 cell limit.")
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("JSON dataset must be valid UTF-8 JSON.") from exc
    profiles = {name: _new_profile() for name in columns}
    profile_limit = min(MAX_PROFILE_ROWS, max(1, MAX_PROFILE_CELLS // max(1, len(columns))))
    for index, row in enumerate(_iter_json_array(text)):
        if index >= profile_limit:
            break
        for name in columns:
            _add_profile_value(profiles[name], row.get(name))
    profiled_rows = min(row_count, profile_limit)
    return {"row_count": row_count, "columns": columns, "profile": _finish_profiles(profiles),
            "profiled_row_count": profiled_rows, "profile_complete": row_count <= profile_limit,
            "profile_method": "streaming_array_first_rows_heuristic"}


def _iter_json_array(text: str):
    """Decode a top-level array one object at a time without retaining all rows."""
    def reject_constant(value: str):
        raise ValueError(f"JSON dataset cannot contain non-finite number {value}.")

    def unique_object(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON dataset objects cannot contain duplicate keys.")
            result[key] = value
        return result

    decoder = json.JSONDecoder(parse_constant=reject_constant, object_pairs_hook=unique_object)
    length = len(text)
    position = 0
    while position < length and text[position].isspace():
        position += 1
    if position >= length or text[position] != "[":
        raise ValueError("JSON dataset must be a list of object rows.")
    position += 1
    first = True
    while True:
        while position < length and text[position].isspace():
            position += 1
        if position < length and text[position] == "]":
            position += 1
            break
        if not first:
            if position >= length or text[position] != ",":
                raise ValueError("JSON dataset array has invalid separators.")
            position += 1
            while position < length and text[position].isspace():
                position += 1
            if position < length and text[position] == "]":
                raise ValueError("JSON dataset array cannot have a trailing comma.")
        if position >= length:
            raise ValueError("JSON dataset array is incomplete.")
        value, position = decoder.raw_decode(text, position)
        yield value
        first = False
    while position < length and text[position].isspace():
        position += 1
    if position != length:
        raise ValueError("JSON dataset has trailing content after its row array.")


def _validate_json_value(value: object) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, float) or (isinstance(current, int) and not isinstance(current, bool)):
            _validate_numeric_value(current)


def _new_profile() -> dict:
    return {"inferred_type": "empty", "missing_count": 0, "nonmissing_count": 0,
            "minimum": None, "maximum": None, "mean": None, "_numeric_count": 0, "_kinds": set()}


def _add_profile_value(profile: dict, value: object, csv_mode: bool = False) -> None:
    if value is None or value == "":
        profile["missing_count"] += 1
        return
    if csv_mode:
        normalized = value.strip()
        lowered = normalized.casefold()
        if lowered in {"true", "false"}:
            kind, numeric = "boolean", None
        else:
            try:
                numeric = float(normalized)
                if not math.isfinite(numeric): raise ValueError
                kind = "integer" if re.fullmatch(r"[+-]?\d+", normalized) else "number"
            except ValueError:
                kind, numeric = "text", None
    elif isinstance(value, bool):
        kind, numeric = "boolean", None
    elif isinstance(value, int):
        kind = "integer"
        try:
            numeric = float(value)
        except OverflowError as exc:
            raise ValueError("JSON dataset integer is outside the supported numeric profile range.") from exc
    elif isinstance(value, float):
        if not math.isfinite(value): raise ValueError("JSON dataset numeric values must be finite.")
        kind, numeric = "number", value
    elif isinstance(value, str):
        kind, numeric = "text", None
    else:
        kind, numeric = "object", None
    profile["nonmissing_count"] += 1
    profile["_kinds"].add(kind)
    if numeric is not None:
        count = profile["_numeric_count"]
        profile["_numeric_count"] = count + 1
        profile["minimum"] = numeric if profile["minimum"] is None else min(profile["minimum"], numeric)
        profile["maximum"] = numeric if profile["maximum"] is None else max(profile["maximum"], numeric)
        profile["mean"] = numeric if count == 0 else profile["mean"] * (count / (count + 1)) + numeric / (count + 1)


def _validate_numeric_value(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON dataset numeric values must be finite.")
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            if not math.isfinite(float(value)):
                raise ValueError("JSON dataset numeric values must fit within the supported profile range.")
        except OverflowError as exc:
            raise ValueError("JSON dataset numeric values must fit within the supported profile range.") from exc


def _finish_profiles(profiles: dict) -> dict:
    finished = {}
    for name, profile in profiles.items():
        kinds = profile.pop("_kinds")
        numeric_count = profile.pop("_numeric_count")
        if not kinds:
            inferred = "empty"
        elif kinds <= {"integer", "number"}:
            inferred = "integer" if kinds == {"integer"} else "number"
        elif len(kinds) == 1:
            inferred = next(iter(kinds))
        else:
            inferred = "mixed"
        if inferred not in {"integer", "number"} or numeric_count != profile["nonmissing_count"]:
            profile.update({"minimum": None, "maximum": None, "mean": None})
        profile["inferred_type"] = inferred
        finished[name] = profile
    return finished


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

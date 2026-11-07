"""Durable experiment execution records with explicit failure states."""

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
import uuid

from researchos.experiments.datasets import dataset_path, get_dataset
from researchos.experiments.sandbox import MAX_CODE_BYTES, DockerSandboxRunner
from researchos.experiments.notebooks import prepare_notebook


def list_experiments(output_dir: str | Path) -> list[dict]:
    folder = Path(output_dir) / "experiments"
    if not folder.exists():
        return []
    records = []
    for path in folder.glob("*/record.json"):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)


def get_experiment(output_dir: str | Path, experiment_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f-]{36}", experiment_id):
        raise ValueError("Invalid experiment identifier.")
    path = Path(output_dir) / "experiments" / experiment_id / "record.json"
    if not path.is_file():
        raise FileNotFoundError("Experiment not found.")
    return json.loads(path.read_text(encoding="utf-8"))


def experiment_artifact_path(output_dir: str | Path, experiment_id: str, filename: str) -> Path:
    """Return only a recorded regular-file artifact whose content hash still matches."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", filename):
        raise ValueError("Invalid experiment artifact filename.")
    record = get_experiment(output_dir, experiment_id)
    item = next((entry for entry in record.get("artifacts", []) if entry.get("filename") == filename), None)
    if item is None:
        raise FileNotFoundError("Experiment artifact not found.")
    path = Path(output_dir) / "experiments" / experiment_id / "artifacts" / filename
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size != item.get("byte_size"):
        raise RuntimeError("Stored experiment artifact failed its integrity check.")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != item.get("sha256"):
        raise RuntimeError("Stored experiment artifact failed its integrity check.")
    return path


def run_experiment(output_dir: str | Path, payload: dict, runner: DockerSandboxRunner | None = None) -> dict:
    """Run only through the isolated runner and persist a result or explicit failure."""
    if not isinstance(payload, dict) or set(payload) - {"title", "source", "notebook", "dataset_ids", "seed", "dependencies"}:
        raise ValueError("Experiment request contains unsupported fields.")
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError("Experiment title is required and limited to 200 characters.")
    has_source, has_notebook = "source" in payload, "notebook" in payload
    if has_source == has_notebook:
        raise ValueError("Provide exactly one of Python source or a Jupyter notebook.")
    notebook_bytes = None
    notebook_provenance = None
    if has_notebook:
        source, notebook_bytes, notebook_provenance = prepare_notebook(payload["notebook"])
    else:
        source = payload["source"]
        if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > MAX_CODE_BYTES:
            raise ValueError("Python experiment source must contain 1 to 512000 UTF-8 bytes.")
    dataset_ids = payload.get("dataset_ids", [])
    if not isinstance(dataset_ids, list) or len(dataset_ids) > 20 or any(not isinstance(item, str) for item in dataset_ids):
        raise ValueError("Select up to 20 valid dataset identifiers.")
    if len(dataset_ids) != len(set(dataset_ids)):
        raise ValueError("Dataset identifiers must be unique.")
    datasets = [get_dataset(output_dir, item) for item in dataset_ids]
    seed = payload.get("seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 4_294_967_295):
        raise ValueError("Experiment seed must be an integer from 0 to 4294967295.")
    dependencies = payload.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise ValueError("Experiment dependencies must be a JSON object of exact package pins.")
    if runner is not None:
        runner.approve_dependencies(dependencies)
    elif dependencies:
        raise ValueError("Package dependencies require a trusted local sandbox image manifest.")

    experiment_id = str(uuid.uuid4())
    folder = Path(output_dir) / "experiments" / experiment_id
    folder.mkdir(parents=True, exist_ok=False)
    source_bytes = source.encode("utf-8")
    source_path = folder / "source.py"
    source_path.write_bytes(source_bytes)
    source_path.chmod(0o444)
    if notebook_bytes is not None:
        notebook_path = folder / "source.ipynb"
        notebook_path.write_bytes(notebook_bytes)
        notebook_path.chmod(0o444)
    now = datetime.now(timezone.utc).isoformat()
    sandbox_limits = asdict(runner.limits) if runner is not None else None
    record = {"experiment_id": experiment_id, "title": title.strip(), "status": "running",
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(), "source_file": "source.py",
        "dataset_ids": dataset_ids, "dataset_sha256": [item["sha256"] for item in datasets],
        "seed": seed, "seed_scope": "Python random and PYTHONHASHSEED only; other libraries require explicit researcher seeding",
        "requested_dependencies": dependencies,
        "notebook": notebook_provenance,
        "requested_environment": {"backend": "docker", "image": getattr(runner, "image", None),
            "python_version": runner.profile.python_version if runner is not None and runner.profile else None,
            "manifest_sha256": runner.profile.manifest_sha256 if runner is not None and runner.profile else None,
            "limits": sandbox_limits, "network": "disabled", "credentials_passed": False,
            "dependency_image_manifest_is_operator_attestation": True},
        "created_at": now, "started_at": now, "completed_at": None,
        "execution": None, "artifacts": [], "failure": None}
    _write_record(folder, record)
    try:
        if runner is None:
            raise RuntimeError("Isolated execution is not configured; no host execution fallback is available.")
        paths = [dataset_path(output_dir, item) for item in dataset_ids]
        execution = runner.run_python(source, paths, seed=seed, dependencies=dependencies,
                                      artifact_dir=folder / "artifacts")
        if not isinstance(execution, dict) or execution.get("status") not in {"succeeded", "failed"}:
            raise RuntimeError("Sandbox runner returned an invalid execution record.")
        if execution["status"] == "succeeded" and execution.get("exit_code") != 0:
            raise RuntimeError("Sandbox runner returned success without a zero exit status.")
        record["execution"] = execution
        record["artifacts"] = execution.get("artifacts", [])
        record["status"] = execution["status"]
        if execution["status"] != "succeeded":
            record["failure"] = "Sandbox execution did not complete successfully."
    except (RuntimeError, OSError, ValueError) as exc:
        record["status"] = "failed"
        record["failure"] = str(exc)[:2000]
    record["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_record(folder, record)
    return record


def _write_record(folder: Path, record: dict) -> None:
    temp = folder / "record.json.tmp"
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(folder / "record.json")

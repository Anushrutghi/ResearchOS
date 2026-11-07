"""Fail-closed Docker runner for researcher-supplied Python code.

The host Docker daemon and a locally installed digest-pinned image are trusted
components. This runner never falls back to executing code in the host Python.
"""

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid


IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9._/:+-]+@sha256:[a-f0-9]{64}$")
PACKAGE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
PACKAGE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]{0,79}$")
MAX_PROFILE_BYTES = 64_000
MAX_CODE_BYTES = 512_000
MAX_OUTPUT_BYTES = 1_000_000
MAX_ARTIFACT_FILES = 20
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_ARCHIVE_BYTES = 40 * 1024 * 1024
RESOURCE_SAMPLE_INTERVAL = 0.5


def _parse_docker_stats(raw: bytes) -> dict | None:
    """Parse one Docker CLI JSON stats line into bounded numeric observations."""
    try:
        value = json.loads(raw.decode("utf-8"))
        cpu_match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)%\s*", value.get("CPUPerc", ""))
        memory = value.get("MemUsage", "").split(" / ", 1)
        if not cpu_match or len(memory) != 2:
            return None
        used = _parse_docker_bytes(memory[0])
        limit = _parse_docker_bytes(memory[1])
        if used is None or limit is None or limit <= 0:
            return None
        return {"cpu_percent": float(cpu_match.group(1)), "memory_bytes": used,
                "memory_limit_bytes": limit, "memory_percent": round(100 * used / limit, 2)}
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return None


def _parse_docker_bytes(value: str) -> int | None:
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*", value)
    if not match:
        return None
    factors = {"B": 1, "kB": 1000, "KB": 1000, "MB": 1000**2, "GB": 1000**3,
               "TB": 1000**4, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    return int(float(match.group(1)) * factors[match.group(2)])


class _DockerResourceSampler:
    """Sample container CPU and memory while the Docker CLI run is active."""

    def __init__(self, docker_command: str, container_name: str):
        self.command = docker_command
        self.container_name = container_name
        self.stop_event = threading.Event()
        self.samples: list[dict] = []
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> dict:
        self.stop_event.set()
        self.thread.join(timeout=2)
        if not self.samples:
            return {"source": "docker_stats_sampled", "sample_interval_seconds": RESOURCE_SAMPLE_INTERVAL,
                    "sample_count": 0, "available": False}
        return {"source": "docker_stats_sampled", "sample_interval_seconds": RESOURCE_SAMPLE_INTERVAL,
                "sample_count": len(self.samples), "available": True,
                "max_cpu_percent": round(max(item["cpu_percent"] for item in self.samples), 2),
                "max_memory_bytes": max(item["memory_bytes"] for item in self.samples),
                "memory_limit_bytes": self.samples[-1]["memory_limit_bytes"],
                "max_memory_percent": round(max(item["memory_percent"] for item in self.samples), 2)}

    def _sample(self) -> None:
        while not self.stop_event.is_set() and len(self.samples) < 1200:
            try:
                result = subprocess.run([self.command, "stats", "--no-stream", "--format", "{{json .}}",
                                         self.container_name], capture_output=True, timeout=2, check=False)
                if result.returncode == 0:
                    parsed = _parse_docker_stats(result.stdout.strip())
                    if parsed is not None:
                        self.samples.append(parsed)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self.stop_event.wait(RESOURCE_SAMPLE_INTERVAL)


def _persist_artifact_archive(raw_archive: bytes, artifact_dir: Path) -> list[dict]:
    """Validate a Docker tar stream fully before writing any untrusted member to disk."""
    if not isinstance(raw_archive, bytes) or len(raw_archive) > MAX_ARTIFACT_ARCHIVE_BYTES:
        raise ValueError("Sandbox artifact archive exceeds its 40 MiB transfer limit.")
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw_archive), mode="r:")
        entries = archive.getmembers()
    except (tarfile.TarError, OSError) as exc:
        raise ValueError("Sandbox artifact archive is malformed.") from exc
    if len(entries) > MAX_ARTIFACT_FILES + 1:
        raise ValueError(f"Sandbox produced more than {MAX_ARTIFACT_FILES} artifacts.")
    validated = []
    names = set()
    total = 0
    for entry in entries:
        if entry.name in {".", "./"} and entry.isdir():
            continue
        if not entry.isfile() or "/" in entry.name or "\\" in entry.name or \
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", entry.name):
            raise ValueError("Sandbox artifacts must be flat regular files with safe names; links and directories are rejected.")
        if entry.name in names:
            raise ValueError("Sandbox artifact archive contains duplicate filenames.")
        names.add(entry.name)
        total += entry.size
        if total > MAX_ARTIFACT_BYTES:
            raise ValueError(f"Sandbox artifacts exceed the {MAX_ARTIFACT_BYTES // (1024 * 1024)} MiB retention limit.")
        validated.append(entry)
    if not validated:
        return []
    artifact_dir.parent.mkdir(parents=True, exist_ok=True)
    if artifact_dir.exists():
        raise ValueError("Experiment artifact destination already exists.")
    artifact_dir.mkdir()
    try:
        records = []
        for entry in validated:
            source = archive.extractfile(entry)
            if source is None:
                raise ValueError("Sandbox artifact archive has an unreadable file entry.")
            content = source.read(entry.size + 1)
            if len(content) != entry.size:
                raise ValueError("Sandbox artifact archive size does not match its file entries.")
            destination = artifact_dir / entry.name
            with destination.open("xb") as output:
                output.write(content)
            destination.chmod(0o444)
            records.append({"filename": entry.name, "byte_size": entry.size,
                            "sha256": hashlib.sha256(content).hexdigest(), "path": f"artifacts/{entry.name}"})
        return records
    except (OSError, ValueError):
        for child in artifact_dir.iterdir():
            child.unlink(missing_ok=True)
        artifact_dir.rmdir()
        raise


def _capture_bounded_command(command: list[str], timeout_seconds: int,
                             max_stdout_bytes: int, max_stderr_bytes: int = 64_000) -> bytes:
    """Capture a command's streams under byte and time limits."""
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise RuntimeError("Docker artifact transfer could not start the Docker client.") from exc
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = False
    timed_out = False
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        selector.register(stream, selectors.EVENT_READ, name)
        os.set_blocking(stream.fileno(), False)
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                process.kill()
                break
            for key, _ in selector.select(min(0.1, remaining)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                limit = max_stdout_bytes if key.data == "stdout" else max_stderr_bytes
                buffer = output[key.data]
                if len(buffer) + len(chunk) > limit:
                    if len(buffer) < limit:
                        buffer.extend(chunk[:limit - len(buffer)])
                    exceeded = True
                    process.kill()
                    break
                buffer.extend(chunk)
            if exceeded:
                break
        process.wait(timeout=2)
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()
    if timed_out or exceeded or process.returncode:
        raise RuntimeError("Sandbox artifact archive transfer failed or exceeded its limits.")
    return bytes(output["stdout"])


@dataclass(frozen=True)
class SandboxProfile:
    image: str
    python_version: str
    packages: dict[str, str]
    manifest_sha256: str

    def approve(self, requested: dict[str, str]) -> None:
        _validate_dependencies(requested)
        missing = {name: version for name, version in requested.items()
                   if self.packages.get(name.casefold()) != version}
        if missing:
            missing_text = ", ".join(f"{name}=={version}" for name, version in sorted(missing.items()))
            raise ValueError(f"Requested packages are not pinned in the sandbox image manifest: {missing_text}.")


def load_sandbox_profile(path: str | Path, image: str) -> SandboxProfile:
    profile_path = Path(path).resolve(strict=True)
    if not profile_path.is_file() or profile_path.stat().st_size > MAX_PROFILE_BYTES:
        raise ValueError("Sandbox image manifest must be a local file no larger than 64 KB.")
    raw = profile_path.read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Sandbox image manifest must be valid UTF-8 JSON.") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "image", "python_version", "packages"}:
        raise ValueError("Sandbox image manifest must contain exactly schema_version, image, python_version, and packages.")
    if manifest.get("schema_version") != 1 or manifest.get("image") != image or not IMAGE_PATTERN.fullmatch(image):
        raise ValueError("Sandbox image manifest must match the configured digest-pinned image and schema version 1.")
    python_version = manifest.get("python_version")
    if not isinstance(python_version, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", python_version):
        raise ValueError("Sandbox image manifest must pin a Python version such as 3.12.4.")
    packages = manifest.get("packages")
    if not isinstance(packages, dict) or len(packages) > 100:
        raise ValueError("Sandbox image manifest packages must be an object of up to 100 exact pins.")
    normalized = {}
    for name, version in packages.items():
        if not isinstance(name, str) or not PACKAGE_NAME_PATTERN.fullmatch(name.casefold()) or \
                name.casefold() in normalized or not isinstance(version, str) or not PACKAGE_VERSION_PATTERN.fullmatch(version):
            raise ValueError("Sandbox image packages must use normalized names and exact version strings.")
        normalized[name.casefold()] = version
    return SandboxProfile(image, python_version, normalized, hashlib.sha256(raw).hexdigest())


def _validate_dependencies(dependencies: object) -> None:
    if not isinstance(dependencies, dict) or len(dependencies) > 50:
        raise ValueError("Experiment dependencies must be an object containing up to 50 exact package pins.")
    normalized = set()
    for name, version in dependencies.items():
        if not isinstance(name, str) or not PACKAGE_NAME_PATTERN.fullmatch(name.casefold()) or \
                name.casefold() in normalized or not isinstance(version, str) or not PACKAGE_VERSION_PATTERN.fullmatch(version):
            raise ValueError("Experiment dependencies must use unique normalized names and exact version strings.")
        normalized.add(name.casefold())


@dataclass(frozen=True)
class ExecutionLimits:
    timeout_seconds: int = 60
    memory: str = "512m"
    cpus: str = "1.0"
    pids: int = 64
    tmp_size: str = "64m"

    def validate(self) -> None:
        if not isinstance(self.timeout_seconds, int) or isinstance(self.timeout_seconds, bool) or not 1 <= self.timeout_seconds <= 600:
            raise ValueError("Execution timeout must be between 1 and 600 seconds.")
        if self.memory not in {"256m", "512m", "1g", "2g"}:
            raise ValueError("Unsupported sandbox memory limit.")
        if self.cpus not in {"0.5", "1.0", "2.0"}:
            raise ValueError("Unsupported sandbox CPU limit.")
        if not isinstance(self.pids, int) or isinstance(self.pids, bool) or not 16 <= self.pids <= 256:
            raise ValueError("Sandbox PID limit must be between 16 and 256.")
        if self.tmp_size not in {"32m", "64m", "128m"}:
            raise ValueError("Unsupported sandbox temporary storage limit.")


class DockerSandboxRunner:
    def __init__(self, image: str, docker_command: str = "docker", limits: ExecutionLimits | None = None,
                 profile: SandboxProfile | None = None):
        if not isinstance(image, str) or not IMAGE_PATTERN.fullmatch(image):
            raise ValueError("Sandbox image must be pinned by a full sha256 digest.")
        self.image = image
        self.docker_command = docker_command
        self.limits = limits or ExecutionLimits()
        self.profile = profile
        if profile is not None and profile.image != image:
            raise ValueError("Sandbox image manifest and runner image do not match.")
        self.limits.validate()

    @property
    def approved_packages(self) -> dict[str, str]:
        return self.profile.packages if self.profile else {}

    def approve_dependencies(self, dependencies: dict[str, str]) -> None:
        _validate_dependencies(dependencies)
        if dependencies and self.profile is None:
            raise ValueError("Package dependencies require a trusted local sandbox image manifest.")
        if self.profile:
            self.profile.approve(dependencies)

    def run_python(self, source: str, datasets: list[str | Path] | None = None, seed: int | None = None,
                   dependencies: dict[str, str] | None = None,
                   artifact_dir: str | Path | None = None) -> dict:
        if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > MAX_CODE_BYTES:
            raise ValueError("Python experiment source must contain 1 to 512000 UTF-8 bytes.")
        self.approve_dependencies(dependencies or {})
        if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 4_294_967_295):
            raise ValueError("Experiment seed must be an integer from 0 to 4294967295.")
        dataset_paths = [Path(path).resolve(strict=True) for path in (datasets or [])]
        if len(dataset_paths) > 20 or any(not path.is_file() for path in dataset_paths):
            raise ValueError("Provide up to 20 existing dataset files.")
        try:
            inspected = subprocess.run([self.docker_command, "image", "inspect", self.image],
                capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Isolated execution is unavailable: Docker image inspection failed.") from exc
        if inspected.returncode != 0:
            raise RuntimeError("Isolated execution is unavailable: the pinned image is not installed locally; image pulls are disabled.")

        container_name = "researchos-" + uuid.uuid4().hex
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="researchos-run-") as stage:
            stage_path = Path(stage)
            if "," in str(stage_path):
                raise RuntimeError("Isolated execution staging path contains an unsupported comma character.")
            stage_path.chmod(0o755)
            script = stage_path / "main.py"
            wrapped_source = source if seed is None else f"import random\nrandom.seed({seed})\n" + source
            script.write_text(wrapped_source, encoding="utf-8")
            script.chmod(0o444)
            dataset_mounts = []
            for index, path in enumerate(dataset_paths):
                target = f"/datasets/{index}{path.suffix.casefold()}"
                if path.suffix.casefold() not in {".csv", ".json"} or "," in str(path):
                    raise ValueError("Sandbox datasets must use supported CSV/JSON paths without comma characters.")
                dataset_mounts.extend(["--mount", f"type=bind,src={path},dst={target},readonly"])
            limits = self.limits
            start_command = [self.docker_command, "run", "--detach", "--name", container_name,
                "--pull=never", "--network=none", "--read-only", "--user=65534:65534",
                "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit", str(limits.pids),
                "--memory", limits.memory, "--cpus", limits.cpus,
                "--tmpfs", f"/tmp:rw,noexec,nosuid,size={limits.tmp_size}",
                "--tmpfs", "/artifacts:rw,noexec,nosuid,size=32m",
                "--mount", f"type=bind,src={stage_path},dst=/workspace,readonly", *dataset_mounts,
                "--workdir=/workspace", "--env=PYTHONDONTWRITEBYTECODE=1", *([f"--env=PYTHONHASHSEED={seed}"] if seed is not None else []), self.image,
                "python3", "-c", "import time; time.sleep(3600)"]
            try:
                try:
                    started_container = subprocess.run(start_command, capture_output=True, timeout=15, check=False)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise RuntimeError("Isolated execution container could not start.") from exc
                if started_container.returncode != 0:
                    raise RuntimeError("Isolated execution container could not start with the pinned image and limits.")
                container_id = started_container.stdout.decode("ascii", errors="ignore").strip()
                if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
                    raise RuntimeError("Docker did not return a valid isolated container identifier.")
                exec_command = [self.docker_command, "exec", "--user=65534:65534", container_name,
                                "python3", "/workspace/main.py"]
                result = self._run_bounded(exec_command, container_name, limits.timeout_seconds)
                try:
                    if artifact_dir is not None and result["status"] == "succeeded":
                        archive = self._copy_artifact_archive(container_name)
                        result["artifacts"] = _persist_artifact_archive(archive, Path(artifact_dir))
                    else:
                        result["artifacts"] = []
                except (OSError, subprocess.TimeoutExpired, ValueError, RuntimeError) as exc:
                    result["status"] = "failed"
                    result["artifacts"] = []
                    result["artifact_failure"] = str(exc)[:1000]
                result["container_id"] = container_id
            finally:
                self._remove_container(container_name)
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        result["image"] = self.image
        return result

    def _remove_container(self, name: str) -> None:
        try:
            subprocess.run([self.docker_command, "rm", "-f", name], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _copy_artifact_archive(self, container_name: str) -> bytes:
        exporter = "import os,sys,tarfile\n" \
            "with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:\n" \
            " for entry in os.scandir('/artifacts'):\n" \
            "  archive.add(entry.path,arcname=entry.name,recursive=False)\n"
        command = [self.docker_command, "exec", "--user=65534:65534", container_name,
                   "python3", "-c", exporter]
        return _capture_bounded_command(command, timeout_seconds=15,
                                        max_stdout_bytes=MAX_ARTIFACT_ARCHIVE_BYTES)

    def _run_bounded(self, command: list[str], container_name: str, timeout_seconds: int) -> dict:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise RuntimeError("Isolated execution could not start the Docker client.") from exc
        sampler = _DockerResourceSampler(self.docker_command, container_name)
        sampler.start()
        selector = selectors.DefaultSelector()
        output = {"stdout": bytearray(), "stderr": bytearray()}
        overflow = False
        timed_out = False
        for stream_name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            selector.register(stream, selectors.EVENT_READ, stream_name)
            os.set_blocking(stream.fileno(), False)
        deadline = time.monotonic() + timeout_seconds
        try:
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    process.kill()
                    break
                for key, _ in selector.select(min(0.1, remaining)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    buffer = output[key.data]
                    available = MAX_OUTPUT_BYTES - sum(len(value) for value in output.values())
                    if len(chunk) > available:
                        if available > 0:
                            buffer.extend(chunk[:available])
                        overflow = True
                        process.kill()
                        break
                    buffer.extend(chunk)
                if overflow:
                    break
            process.wait(timeout=2)
        finally:
            selector.close()
            if process.poll() is None:
                process.kill()
                process.wait()
            for stream in (process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
            if timed_out or overflow:
                try:
                    subprocess.run([self.docker_command, "rm", "-f", container_name],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            resource_usage = sampler.stop()
        return {"status": "failed" if timed_out or overflow or process.returncode else "succeeded",
                "exit_code": process.returncode, "timed_out": timed_out, "output_limit_exceeded": overflow,
                "stdout": output["stdout"].decode("utf-8", errors="replace"),
                "stderr": output["stderr"].decode("utf-8", errors="replace"),
                "resource_usage": resource_usage,
                "duration_seconds": 0.0}

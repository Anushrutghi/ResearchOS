import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from researchos.experiments.datasets import dataset_path, get_dataset, import_dataset, list_datasets, record_dataset_transformation
from researchos.experiments.analysis import evaluate_prediction_columns, list_evaluations
from researchos.experiments.sandbox import DockerSandboxRunner, ExecutionLimits, _parse_docker_stats, _persist_artifact_archive, load_sandbox_profile
from researchos.experiments.store import experiment_artifact_path, get_experiment, list_experiments, run_experiment
from researchos.experiments.notebooks import prepare_notebook


IMAGE = "local/research-python@sha256:" + "a" * 64


def make_artifact_archive(files, symlinks=()):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
        for name, target in symlinks:
            item = tarfile.TarInfo(name)
            item.type = tarfile.SYMTYPE
            item.linkname = target
            archive.addfile(item)
    return buffer.getvalue()


class DatasetStoreTests(unittest.TestCase):
    def test_csv_is_content_addressed_and_transformation_lineage_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            original = b"model,score\na,0.7\nb,0.8\n"
            imported = import_dataset(directory, "scores.csv", original)
            self.assertEqual(imported["sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(imported["summary"]["row_count"], 2)
            self.assertEqual(imported["summary"]["columns"], ["model", "score"])
            self.assertEqual(imported["summary"]["profile"]["score"]["inferred_type"], "number")
            self.assertEqual(imported["summary"]["profile"]["score"]["minimum"], 0.7)
            self.assertEqual(imported["summary"]["profiled_row_count"], 2)
            self.assertTrue(imported["summary"]["profile_complete"])
            self.assertEqual(dataset_path(directory, imported["dataset_id"]).read_bytes(), original)
            transformed_bytes = b"model,score\nb,0.8\n"
            transformed = record_dataset_transformation(directory, [imported["dataset_id"]], "filtered.csv",
                transformed_bytes, "Exclude rows below the prespecified score threshold")
            self.assertEqual(transformed["parent_dataset_ids"], [imported["dataset_id"]])
            self.assertEqual(transformed["transformation_description"], "Exclude rows below the prespecified score threshold")
            self.assertEqual(len(list_datasets(directory)), 2)
            self.assertEqual(get_dataset(directory, imported["dataset_id"])["sha256"], imported["sha256"])

    def test_dataset_validation_rejects_malformed_rows_and_untrusted_types(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "row 2"):
                import_dataset(directory, "bad.csv", b"a,b\n1\n")
            with self.assertRaisesRegex(ValueError, "list of.*object rows"):
                import_dataset(directory, "bad.json", b'{"one": 1}')
            with self.assertRaisesRegex(ValueError, "Only CSV and JSON"):
                import_dataset(directory, "code.py", b"print('unsafe')")
            with self.assertRaisesRegex(ValueError, "duplicate keys"):
                import_dataset(directory, "duplicate.json", b'[{"x":1,"x":2}]')
            with self.assertRaisesRegex(ValueError, "finite"):
                import_dataset(directory, "nested.json", b'[{"x":{"nested":[1e999]}}]')
            with patch("researchos.experiments.datasets.MAX_DATASET_CELLS", 3):
                with self.assertRaisesRegex(ValueError, "cell limit"):
                    import_dataset(directory, "too-many-cells.json", b'[{"a":1,"b":2},{"a":3,"b":4}]')

    def test_json_row_array_is_profiled_incrementally_and_rejects_trailing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            imported = import_dataset(directory, "rows.json", b'[{"a":1},{"b":2},{"a":3,"b":4}]')
            self.assertEqual(imported["summary"]["row_count"], 3)
            self.assertEqual(imported["summary"]["profile_method"], "streaming_array_first_rows_heuristic")
            self.assertEqual(imported["summary"]["profile"]["a"]["missing_count"], 1)
            self.assertEqual(imported["summary"]["profile"]["b"]["missing_count"], 1)
            with self.assertRaisesRegex(ValueError, "trailing content"):
                import_dataset(directory, "trailing.json", b'[{"a":1}] false')

    def test_classification_and_regression_metrics_include_explicit_baselines(self):
        with tempfile.TemporaryDirectory() as directory:
            classified = import_dataset(directory, "class.csv",
                b"actual,predicted\ncat,cat\ndog,dog\ncat,dog\ndog,dog\n")
            result = evaluate_prediction_columns(directory, classified["dataset_id"], "classification", "actual", "predicted")
            self.assertEqual(result["row_count"], 4)
            self.assertEqual(result["metrics"]["accuracy"], 0.75)
            self.assertEqual(result["metrics"]["majority_class_baseline_accuracy"], 0.5)
            self.assertEqual(len(result["metrics"]["confusion_matrix"]), 2)
            self.assertEqual(result["visualization"]["type"], "confusion_matrix")
            self.assertIn("no inferential comparison", result["warning"])

            regressed = import_dataset(directory, "regression.csv", b"actual,predicted\n1,1\n3,2\n5,7\n")
            result = evaluate_prediction_columns(directory, regressed["dataset_id"], "regression", "actual", "predicted")
            self.assertEqual(result["metrics"]["mae"], 1.0)
            self.assertAlmostEqual(result["metrics"]["rmse"], (5 / 3) ** 0.5)
            self.assertAlmostEqual(result["metrics"]["r2"], 0.375)
            self.assertEqual(result["visualization"]["type"], "actual_predicted_scatter")
            self.assertEqual(len(result["visualization"]["points"]), 3)
            self.assertEqual(len(list_evaluations(directory)), 2)

    def test_prediction_metrics_reject_missing_values_and_non_numeric_regression(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = import_dataset(directory, "missing.csv", b"a,p\nx,\n")
            with self.assertRaisesRegex(ValueError, "missing actual/predicted"):
                evaluate_prediction_columns(directory, missing["dataset_id"], "classification", "a", "p")
            invalid = import_dataset(directory, "invalid.csv", b"a,p\none,nope\n")
            with self.assertRaisesRegex(ValueError, "numeric values"):
                evaluate_prediction_columns(directory, invalid["dataset_id"], "regression", "a", "p")


class SandboxRunnerTests(unittest.TestCase):
    def test_artifact_persistence_is_flat_bounded_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "saved-artifacts"
            items = _persist_artifact_archive(make_artifact_archive({"result.csv": b"score\n1\n"}), destination)
            self.assertEqual(items[0]["filename"], "result.csv")
            self.assertEqual(items[0]["byte_size"], 8)
            self.assertEqual((destination / "result.csv").read_bytes(), b"score\n1\n")

            bad = make_artifact_archive({}, [("escape", "../../outside")])
            with self.assertRaisesRegex(ValueError, "regular files"):
                _persist_artifact_archive(bad, root / "rejected")

    def test_saved_artifact_route_path_checks_recorded_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            experiment_id = "12345678-1234-5678-1234-567812345678"
            folder = root / "experiments" / experiment_id
            items = _persist_artifact_archive(make_artifact_archive({"plot.svg": b"<svg/>"}), folder / "artifacts")
            folder.joinpath("record.json").write_text(json.dumps({"artifacts": items}), encoding="utf-8")
            path = experiment_artifact_path(root, experiment_id, "plot.svg")
            self.assertEqual(path.read_text(encoding="utf-8"), "<svg/>")
            path.chmod(0o644)
            path.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "integrity"):
                experiment_artifact_path(root, experiment_id, "plot.svg")

    def test_docker_stats_parser_returns_numeric_observations_and_rejects_bad_rows(self):
        parsed = _parse_docker_stats(json.dumps({"CPUPerc": "12.50%", "MemUsage": "64MiB / 512MiB"}).encode())
        self.assertEqual(parsed, {"cpu_percent": 12.5, "memory_bytes": 64 * 1024 * 1024,
                                  "memory_limit_bytes": 512 * 1024 * 1024, "memory_percent": 12.5})
        self.assertIsNone(_parse_docker_stats(b"not json"))
        self.assertIsNone(_parse_docker_stats(b'{"CPUPerc":"bad","MemUsage":"1MiB / 2MiB"}'))

    def test_manifest_is_validated_and_dependency_requests_require_exact_approved_pins(self):
        manifest = {"schema_version": 1, "image": IMAGE, "python_version": "3.12.4",
                    "packages": {"numpy": "2.1.3", "pandas": "2.2.3"}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            raw = json.dumps(manifest).encode()
            path.write_bytes(raw)
            profile = load_sandbox_profile(path, IMAGE)
            self.assertEqual(profile.manifest_sha256, hashlib.sha256(raw).hexdigest())
            runner = DockerSandboxRunner(IMAGE, profile=profile)
            runner.approve_dependencies({"numpy": "2.1.3"})
            with self.assertRaisesRegex(ValueError, "not pinned"):
                runner.approve_dependencies({"numpy": "1.0"})
            with self.assertRaisesRegex(ValueError, "match"):
                load_sandbox_profile(path, "local/other@sha256:" + "b" * 64)
            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly"):
                load_sandbox_profile(path, IMAGE)
            manifest["packages"] = {"NumPy": "2.1.3", "numpy": "2.2.0"}
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "normalized"):
                load_sandbox_profile(path, IMAGE)

    def test_runner_requires_digest_pinned_image_and_bounded_limits(self):
        with self.assertRaisesRegex(ValueError, "sha256 digest"):
            DockerSandboxRunner("python:3.12")
        with self.assertRaisesRegex(ValueError, "timeout"):
            DockerSandboxRunner(IMAGE, limits=ExecutionLimits(timeout_seconds=0))

    def test_missing_local_image_fails_before_any_run_command(self):
        inspect = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"missing")
        with patch("researchos.experiments.sandbox.subprocess.run", return_value=inspect) as run, \
             patch("researchos.experiments.sandbox.subprocess.Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "not installed locally"):
                DockerSandboxRunner(IMAGE).run_python("print('must not run')")
        self.assertEqual(run.call_args.args[0][:3], ["docker", "image", "inspect"])
        popen.assert_not_called()

    def test_run_command_enforces_isolation_and_passes_no_host_credentials(self):
        inspect = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"[]", stderr=b"")
        started = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"123456789abc\n", stderr=b"")
        expected = {"status": "succeeded", "exit_code": 0, "timed_out": False,
                    "output_limit_exceeded": False, "stdout": "done", "stderr": "", "duration_seconds": 0.0}
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data.csv"
            data.write_text("x\n1\n", encoding="utf-8")
            def fake_run(command, **kwargs):
                return started if command[1] == "run" else inspect
            with patch("researchos.experiments.sandbox.subprocess.run", side_effect=fake_run) as docker_calls, \
                 patch.object(DockerSandboxRunner, "_run_bounded", return_value=expected) as run:
                result = DockerSandboxRunner(IMAGE).run_python("print('done')", [data])
        start_command = docker_calls.call_args_list[1].args[0]
        for required in ("--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                         "--security-opt=no-new-privileges", "--pids-limit", "--memory", "--cpus"):
            self.assertIn(required, start_command)
        self.assertIn("--user=65534:65534", start_command)
        self.assertIn("/artifacts:rw,noexec,nosuid,size=32m", start_command)
        self.assertIn(IMAGE, start_command)
        exec_command = run.call_args.args[0]
        self.assertEqual(exec_command[:3], ["docker", "exec", "--user=65534:65534"])
        self.assertNotIn("RESEARCHOS_API_KEY", start_command)
        self.assertNotIn("OPENAI_API_KEY", start_command)
        self.assertEqual(result["status"], "succeeded")

    def test_successful_run_copies_only_bounded_artifacts_and_removes_container(self):
        inspect = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"[]", stderr=b"")
        started = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"123456789abc\n", stderr=b"")
        expected = {"status": "succeeded", "exit_code": 0, "timed_out": False,
                    "output_limit_exceeded": False, "stdout": "", "stderr": "",
                    "duration_seconds": 0.01, "resource_usage": {"available": False}}
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return started if command[1] == "run" else inspect

        with tempfile.TemporaryDirectory() as directory, \
             patch("researchos.experiments.sandbox.subprocess.run", side_effect=fake_run), \
             patch.object(DockerSandboxRunner, "_run_bounded", return_value=expected):
            artifact_dir = Path(directory) / "saved"
            runner = DockerSandboxRunner(IMAGE)
            with patch.object(runner, "_copy_artifact_archive", return_value=make_artifact_archive({"summary.json": b'{"ok":true}'})) as copy:
                result = runner.run_python("print(1)", artifact_dir=artifact_dir)
            self.assertNotIn("artifact_failure", result, result)
            self.assertEqual(result["artifacts"][0]["filename"], "summary.json")
            self.assertTrue((artifact_dir / "summary.json").exists())
            self.assertEqual((artifact_dir / "summary.json").read_text(encoding="utf-8"), '{"ok":true}')
            copy.assert_called_once()
            self.assertTrue(any(command[1:3] == ["rm", "-f"] for command in calls))

    def test_output_overflow_and_timeout_return_failed_results(self):
        class FakeStream:
            def __init__(self, chunks): self.chunks = iter(chunks); self.closed = False
            def fileno(self): return id(self) % 100000
            def close(self): self.closed = True
        class FakeProcess:
            def __init__(self, *args, **kwargs):
                self.stdout = FakeStream([b"x" * 100])
                self.stderr = FakeStream([])
                self.returncode = None
                self.killed = False
            def poll(self): return self.returncode
            def kill(self): self.killed = True; self.returncode = -9
            def wait(self, timeout=None): return self.returncode
        runner = DockerSandboxRunner(IMAGE)
        class FakeKey:
            def __init__(self, fileobj, data): self.fileobj, self.data = fileobj, data
        class FakeSelector:
            def __init__(self): self.keys = []
            def register(self, fileobj, events, data): self.keys.append(FakeKey(fileobj, data))
            def get_map(self): return {key.fileobj: key for key in self.keys}
            def select(self, timeout): return [(self.keys[0], None)] if self.keys else []
            def unregister(self, fileobj): self.keys = [key for key in self.keys if key.fileobj is not fileobj]
            def close(self): pass
        with patch("researchos.experiments.sandbox.MAX_OUTPUT_BYTES", 10), \
             patch("researchos.experiments.sandbox.subprocess.Popen", FakeProcess), \
             patch("researchos.experiments.sandbox.subprocess.run"), \
             patch("researchos.experiments.sandbox.selectors.DefaultSelector", FakeSelector), \
             patch("researchos.experiments.sandbox.os.set_blocking"), \
             patch("researchos.experiments.sandbox.os.read", return_value=b"x" * 100):
            result = runner._run_bounded(["docker", "run"], "test-container", 2)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["output_limit_exceeded"])

    def test_timeout_kills_runner_and_requests_container_cleanup(self):
        class FakeStream:
            def __init__(self): self.closed = False
            def fileno(self): return id(self) % 100000
            def close(self): self.closed = True
        class FakeProcess:
            def __init__(self, *args, **kwargs):
                self.stdout, self.stderr, self.returncode = FakeStream(), FakeStream(), None
            def poll(self): return self.returncode
            def kill(self): self.returncode = -9
            def wait(self, timeout=None): return self.returncode
        class FakeKey:
            def __init__(self, fileobj, data): self.fileobj, self.data = fileobj, data
        class FakeSelector:
            def __init__(self): self.keys = []
            def register(self, fileobj, events, data): self.keys.append(FakeKey(fileobj, data))
            def get_map(self): return {key.fileobj: key for key in self.keys}
            def select(self, timeout): return []
            def close(self): pass
        runner = DockerSandboxRunner(IMAGE)
        cleanup = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        with patch("researchos.experiments.sandbox.subprocess.Popen", FakeProcess), \
             patch("researchos.experiments.sandbox.subprocess.run", return_value=cleanup) as run, \
             patch("researchos.experiments.sandbox.selectors.DefaultSelector", FakeSelector), \
             patch("researchos.experiments.sandbox.os.set_blocking"), \
             patch("researchos.experiments.sandbox.time.monotonic", side_effect=[0.0, 2.0]):
            result = runner._run_bounded(["docker", "run"], "timed-container", 1)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["timed_out"])
        self.assertEqual(run.call_args.args[0], ["docker", "rm", "-f", "timed-container"])


class ExperimentRecordTests(unittest.TestCase):
    def test_notebook_cells_run_sequentially_and_original_is_preserved(self):
        notebook = {"nbformat": 4, "metadata": {"kernelspec": {"language": "python"}},
            "cells": [{"cell_type": "markdown", "source": ["# Analysis"]},
                {"cell_type": "code", "source": ["value = 40\n", "print(value + 2)"], "outputs": [{"text": "forged"}]},
                {"cell_type": "code", "source": "print(value + 3)"}]}
        source, original, provenance = prepare_notebook(notebook)
        self.assertIn("value = 40", source)
        self.assertIn("print(value + 3)", source)
        self.assertNotIn("forged", source)
        self.assertEqual(provenance["code_cell_count"], 2)
        self.assertTrue(provenance["outputs_ignored"])
        with tempfile.TemporaryDirectory() as directory:
            class FakeRunner:
                image = IMAGE
                limits = ExecutionLimits()
                profile = None
                def approve_dependencies(self, dependencies): pass
                def run_python(self, source, datasets, seed=None, dependencies=None, artifact_dir=None):
                    self.executed = source
                    return {"status": "succeeded", "exit_code": 0, "stdout": "42\\n45\\n",
                            "stderr": "", "duration_seconds": 0.01}
            runner = FakeRunner()
            record = run_experiment(directory, {"title": "Notebook", "notebook": notebook}, runner)
            folder = Path(directory) / "experiments" / record["experiment_id"]
            self.assertEqual(record["status"], "succeeded")
            self.assertEqual(record["notebook"]["original_sha256"], provenance["original_sha256"])
            self.assertEqual((folder / "source.ipynb").read_bytes(), original)
            self.assertEqual(runner.executed, source)
            self.assertEqual((folder / "source.py").read_text(), source)

    def test_notebook_magics_and_invalid_formats_are_rejected(self):
        for notebook in (
            {"nbformat": 3, "cells": []},
            {"nbformat": 4, "cells": [{"cell_type": "code", "source": "%matplotlib inline"}]},
            {"nbformat": 4, "cells": [{"cell_type": "code", "source": "!pip install package"}]},
            {"nbformat": 4, "metadata": {"kernelspec": {"language": "julia"}}, "cells": []},
        ):
            with self.subTest(notebook=notebook), self.assertRaises(ValueError):
                prepare_notebook(notebook)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            with tempfile.TemporaryDirectory() as directory:
                run_experiment(directory, {"title": "Ambiguous", "source": "x=1", "notebook": {}})

    def test_success_records_execution_provenance_and_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = import_dataset(directory, "data.csv", b"x\n1\n")
            class FakeRunner:
                image = IMAGE
                limits = ExecutionLimits()
                profile = None
                def approve_dependencies(self, dependencies):
                    self.assert_dependencies_approved = dependencies
                def run_python(self, source, datasets, seed=None, dependencies=None, artifact_dir=None):
                    self.assert_source = source
                    self.assert_dataset = datasets[0].read_bytes()
                    self.assert_seed = seed
                    self.assert_dependencies = dependencies
                    return {"status": "succeeded", "exit_code": 0, "stdout": "observed output",
                            "stderr": "", "duration_seconds": 0.02, "image": IMAGE}
            runner = FakeRunner()
            record = run_experiment(directory, {"title": "Offline run", "source": "print(1)",
                "dataset_ids": [dataset["dataset_id"]], "seed": 17}, runner)
            self.assertEqual(record["status"], "succeeded")
            self.assertEqual(record["dataset_sha256"], [dataset["sha256"]])
            self.assertEqual(runner.assert_seed, 17)
            self.assertEqual(runner.assert_dependencies, {})
            self.assertEqual(runner.assert_dataset, b"x\n1\n")
            self.assertEqual(get_experiment(directory, record["experiment_id"]), record)
            self.assertEqual(list_experiments(directory), [record])

    def test_dependency_pins_and_manifest_provenance_are_persisted(self):
        manifest = {"schema_version": 1, "image": IMAGE, "python_version": "3.12.4", "packages": {"numpy": "2.1.3"}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            raw = json.dumps(manifest).encode()
            path.write_bytes(raw)
            runner = DockerSandboxRunner(IMAGE, profile=load_sandbox_profile(path, IMAGE))
            with patch.object(runner, "run_python", return_value={"status": "succeeded", "exit_code": 0,
                    "stdout": "", "stderr": "", "duration_seconds": 0, "image": IMAGE}) as execute:
                record = run_experiment(directory, {"title": "Pinned run", "source": "import numpy",
                    "dependencies": {"numpy": "2.1.3"}}, runner)
            self.assertEqual(record["status"], "succeeded")
            self.assertEqual(record["requested_dependencies"], {"numpy": "2.1.3"})
            self.assertEqual(record["requested_environment"]["python_version"], "3.12.4")
            self.assertEqual(record["requested_environment"]["manifest_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertTrue(record["requested_environment"]["dependency_image_manifest_is_operator_attestation"])
            self.assertEqual(execute.call_args.kwargs["dependencies"], {"numpy": "2.1.3"})
            with self.assertRaisesRegex(ValueError, "not pinned"):
                run_experiment(directory, {"title": "Wrong pin", "source": "x=1", "dependencies": {"numpy": "1"}}, runner)

    def test_missing_sandbox_records_failure_and_never_executes_on_host(self):
        with tempfile.TemporaryDirectory() as directory:
            record = run_experiment(directory, {"title": "Must fail closed", "source": "print('no')"})
            self.assertEqual(record["status"], "failed")
            self.assertIn("no host execution fallback", record["failure"])
            self.assertIsNone(record["execution"])

    def test_unknown_datasets_and_invalid_payloads_are_rejected_before_record_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(FileNotFoundError, "not found"):
                run_experiment(directory, {"title": "Invalid", "source": "x=1", "dataset_ids": ["f" * 64]}, object())
            self.assertEqual(list_experiments(directory), [])


if __name__ == "__main__":
    unittest.main()

"""Opt-in end-to-end checks for a locally installed digest-pinned sandbox image."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from researchos.experiments.sandbox import DockerSandboxRunner, ExecutionLimits, load_sandbox_profile


IMAGE = os.environ.get("RESEARCHOS_DOCKER_TEST_IMAGE")
MANIFEST = os.environ.get("RESEARCHOS_DOCKER_TEST_MANIFEST")


@unittest.skipUnless(IMAGE and MANIFEST, "Set RESEARCHOS_DOCKER_TEST_IMAGE and RESEARCHOS_DOCKER_TEST_MANIFEST for live Docker checks")
class LiveSandboxTests(unittest.TestCase):
    def test_filesystem_network_artifact_telemetry_and_timeout_boundaries(self):
        profile = load_sandbox_profile(MANIFEST, IMAGE)
        runner = DockerSandboxRunner(IMAGE, profile=profile, limits=ExecutionLimits(timeout_seconds=20, memory="512m", cpus="0.5"))
        source = """import json, os, pathlib, platform, socket, stat, time
    dataset = pathlib.Path('/datasets/0.csv')
    dataset_before = dataset.read_bytes()
    try:
        dataset.write_bytes(b'changed')
        dataset_read_only = False
    except OSError:
        dataset_read_only = True
    try:
        pathlib.Path('/workspace/should-not-write').write_text('blocked')
        workspace_read_only = False
    except OSError:
        workspace_read_only = True
    try:
        with socket.create_connection(('1.1.1.1', 443), timeout=1):
            network_blocked = False
    except OSError:
        network_blocked = True
    root_read_only = bool(os.statvfs('/etc').f_flag & getattr(os, 'ST_RDONLY', 1))
    result = {'uid': os.geteuid(), 'dataset_read_only': dataset_read_only,
              'workspace_read_only': workspace_read_only, 'network_blocked': network_blocked,
              'root_read_only': root_read_only, 'dataset': dataset_before.decode(),
              'python_version': platform.python_version()}
    pathlib.Path('/artifacts/live-check.json').write_text(json.dumps(result))
    print(json.dumps(result))
    time.sleep(2)
    """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "input.csv"
            dataset.write_text("value\n42\n", encoding="utf-8")
            artifact_dir = root / "artifacts"
            execution = runner.run_python("\n".join(line[4:] if line.startswith("    ") else line for line in source.splitlines()), [dataset], seed=23, dependencies={}, artifact_dir=artifact_dir)
            self.assertEqual(execution["status"], "succeeded", execution)
            checks = json.loads(execution["stdout"].strip())
            self.assertEqual(checks["uid"], 65534)
            self.assertTrue(checks["dataset_read_only"])
            self.assertTrue(checks["workspace_read_only"])
            self.assertTrue(checks["network_blocked"])
            self.assertTrue(checks["root_read_only"])
            self.assertEqual(checks["dataset"], "value\n42\n")
            self.assertEqual(checks["python_version"], profile.python_version)
            self.assertTrue(execution["artifacts"], execution)
            self.assertEqual(execution["artifacts"][0]["filename"], "live-check.json")
            self.assertEqual(json.loads((artifact_dir / "live-check.json").read_text(encoding="utf-8")), checks)
            self.assertTrue(execution["resource_usage"]["available"], execution["resource_usage"])
            self.assertGreater(execution["resource_usage"]["sample_count"], 0)
            self.assertLessEqual(execution["resource_usage"]["max_memory_bytes"],
                                 execution["resource_usage"]["memory_limit_bytes"])

        timeout_runner = DockerSandboxRunner(IMAGE, profile=profile,
            limits=ExecutionLimits(timeout_seconds=2, memory="256m", cpus="0.5"))
        timeout = timeout_runner.run_python("import time; time.sleep(10)")
        self.assertEqual(timeout["status"], "failed", timeout)
        self.assertTrue(timeout["timed_out"], timeout)
        inspected = subprocess.run(["docker", "inspect", timeout["container_id"]],
                                   capture_output=True, check=False)
        self.assertNotEqual(inspected.returncode, 0, "timed-out container was not removed")


if __name__ == "__main__":
    unittest.main()

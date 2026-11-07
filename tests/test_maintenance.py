import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from researchos.maintenance import create_backup, verify_backup, restore_backup, scientific_quality_report
from researchos.experiments.datasets import import_dataset
from researchos.main import main
from researchos.security.auth import create_user, authenticate
from researchos.agents.defaults import default_agent_registry
from researchos.agents.runtime import WorkflowStore
from researchos.projects.store import ProjectStore


class MaintenanceTests(unittest.TestCase):
    def test_backup_verification_and_restore_into_new_directory(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "research"
            root.mkdir()
            (root / "record.json").write_text('{"title":"traceable"}\n', encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "notes.txt").write_text("kept", encoding="utf-8")
            archive = Path(parent) / "snapshot.tar.gz"
            created = create_backup(root, archive)
            self.assertEqual(created["file_count"], 2)
            verified = verify_backup(archive)
            self.assertTrue(verified["valid"])
            destination = Path(parent) / "restored"
            restored = restore_backup(archive, destination)
            self.assertEqual(restored["file_count"], 2)
            self.assertEqual((destination / "sub" / "notes.txt").read_text(), "kept")
            with self.assertRaisesRegex(ValueError, "must not already exist"):
                restore_backup(archive, destination)
            with self.assertRaisesRegex(ValueError, "outside"):
                create_backup(root, root / "nested.tar.gz")

    def test_backup_rejects_corrupt_content_before_restore(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "research"; root.mkdir()
            (root / "record.txt").write_text("original", encoding="utf-8")
            archive = Path(parent) / "snapshot.tar.gz"
            create_backup(root, archive)
            raw = bytearray(archive.read_bytes()); raw[len(raw) // 2] ^= 0x40; archive.write_bytes(raw)
            with self.assertRaises(ValueError):
                verify_backup(archive)
            with self.assertRaises(ValueError):
                restore_backup(archive, Path(parent) / "restored")

    def test_scientific_quality_report_checks_dataset_integrity_without_quality_score(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = import_dataset(directory, "study.csv", b"group,value\na,1\nb,2\n")
            report = scientific_quality_report(directory)
            self.assertEqual(report["status"], "pass")
            checks = {item["check"]: item for item in report["checks"]}
            self.assertEqual(checks["dataset_content_hashes"]["records_checked"], 1)
            self.assertNotIn("score", report)
            content = Path(directory) / "datasets" / "objects" / f"{dataset['dataset_id']}.csv"
            content.chmod(0o644); content.write_bytes(b"changed")
            changed = scientific_quality_report(directory)
            self.assertEqual(changed["status"], "fail")
            self.assertIn("hash/metadata failures", next(x["detail"] for x in changed["checks"] if x["check"] == "dataset_content_hashes"))

    def test_backup_quality_and_restore_cli_commands(self):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent) / "workspace"; root.mkdir()
            (root / "sample.json").write_text("{}", encoding="utf-8")
            user, token = create_user(root, "admin", "Research Admin")
            workflow_store = WorkflowStore(root, default_agent_registry())
            workflow = workflow_store.create({"title": "Recoverable plan", "tasks": [{"task_id": "plan",
                "agent": "research_plan", "approval_required": True,
                "input": {"question": "How does sleep affect learning?"}}]})
            project = ProjectStore(root).create({"title": "Recovery project"}, user)
            archive = Path(parent) / "snapshot.tar.gz"
            destination = Path(parent) / "restored"
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["backup", "--output-dir", str(root), "--destination", str(archive)]), 0)
                self.assertEqual(main(["verify-backup", str(archive)]), 0)
                self.assertEqual(main(["restore", str(archive), "--destination", str(destination)]), 0)
                self.assertEqual(main(["quality", "--output-dir", str(destination)]), 0)
            self.assertTrue((destination / "sample.json").is_file())
            self.assertEqual(authenticate(destination, token)["username"], "admin")
            recovered_workflow = WorkflowStore(destination, default_agent_registry()).get(workflow["workflow_id"])
            self.assertEqual(recovered_workflow["status"], "awaiting_approval")
            self.assertEqual(ProjectStore(destination).get(project["project_id"], user)["title"], "Recovery project")
            self.assertIn('"valid": true', output.getvalue())


if __name__ == "__main__":
    unittest.main()

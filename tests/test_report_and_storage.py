import tempfile
import unittest
from pathlib import Path

from researchos.documents.markdown import render_report
from researchos.storage.database import save_run


class ReportAndStorageTests(unittest.TestCase):
    def test_report_labels_search_results_as_candidate_literature(self):
        report = render_report({"topic": "T", "research_field": "CS", "output_format": "markdown"}, [], "run-1")
        self.assertIn("Candidate literature", report)
        self.assertIn("No experiment was run", report)

    def test_saves_run_record(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, payload = save_run({"topic": "A topic", "research_field": "CS", "output_format": "markdown"}, [], directory)
            self.assertTrue((folder / "run.json").exists())
            self.assertEqual(payload["works"], [])
            self.assertTrue(Path(folder).name.endswith("-a-topic-" + payload["run_id"].split("-")[-1]))


if __name__ == "__main__":
    unittest.main()

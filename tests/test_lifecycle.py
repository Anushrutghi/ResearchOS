"""Cross-engine v1.0 smoke path using deterministic local inputs only."""

import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from researchos.core.request import create_research_request
from researchos.core.orchestrator import run_research
from researchos.workspace import store_pdf
from researchos.knowledge.store import add_claim, update_claim_review
from researchos.methodology.store import save_study_design, approve_study_design
from researchos.experiments.datasets import import_dataset
from researchos.experiments.analysis import evaluate_prediction_columns
from researchos.experiments.store import run_experiment, get_experiment
from researchos.experiments.sandbox import ExecutionLimits
from researchos.writing.store import create_draft, review_draft, export_draft, get_export_path
from researchos.security.auth import create_user
from researchos.projects.store import ProjectStore
from researchos.projects.lifecycle import project_lifecycle
from researchos.agents.defaults import default_agent_registry
from researchos.agents.runtime import WorkflowStore
from researchos.maintenance import scientific_quality_report
from researchos.maintenance import create_backup, restore_backup


class LifecycleTests(unittest.TestCase):
    def test_research_to_reviewed_export_with_project_and_restart_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            request = create_research_request("How does sleep affect learning?", "Education", "markdown")
            run = run_research(request, output_dir=directory, search=False)
            self.assertTrue(Path(run.report_path).is_file())
            run_record = json.loads((run.directory / "run.json").read_text(encoding="utf-8"))
            availability = {item["name"]: item["availability"] for item in run_record["workflow"]}
            self.assertEqual(availability["Experiments and analysis"], "digest_pinned_docker_required")
            self.assertEqual(availability["Scientific writing"], "researcher_approval_required")

            writer = PdfWriter(); page = writer.add_blank_page(width=612, height=792)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            stream = DecodedStreamObject(); stream.set_data(b"BT /F1 12 Tf 72 720 Td (The study reports an observed learning outcome.) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
            pdf = BytesIO(); writer.write(pdf)
            document = store_pdf(directory, "source.pdf", base64.b64encode(pdf.getvalue()).decode())
            claim = add_claim(directory, document["document_id"], 1,
                "The study reports an observed learning outcome.", "The study reports the observed learning outcome.")
            reviewed = update_claim_review(directory, claim["claim_id"], "reviewed", "Checked against the stored PDF page.")

            design = save_study_design(directory, {"title": "Sleep and learning", "research_field": "Education",
                "research_type": "quantitative", "problem_statement": "Estimate the association between sleep and learning.",
                "objectives": ["Describe the observed association."], "research_questions": ["How are sleep and learning associated?"],
                "methodology": "Observational analysis", "methodology_rationale": "The design records observed rather than assigned exposure.",
                "population_or_material": "Learners in the supplied study", "sample_size": "As reported in the source study",
                "sampling_method": "As reported by the study authors", "variables": {"independent": ["sleep"], "dependent": ["learning"]},
                "analysis_plan": "Report descriptive measures and assumptions.", "evaluation_metrics": ["Observed outcome"],
                "limitations": ["No causal inference is claimed."], "ethics_notes": "Use only the approved source data.",
                "evidence_claim_ids": [reviewed["claim_id"]]})
            design = approve_study_design(directory, design["design_id"], "Required fields and source link reviewed.")

            dataset = import_dataset(directory, "predictions.csv", b"actual,predicted\n0,0\n1,1\n1,0\n")
            evaluation = evaluate_prediction_columns(directory, dataset["dataset_id"], "classification", "actual", "predicted")

            class OfflineRunner:
                image = "python@sha256:" + "a" * 64
                limits = ExecutionLimits()
                profile = None
                def approve_dependencies(self, dependencies):
                    if dependencies: raise AssertionError("This fixture uses no external package dependencies.")
                def run_python(self, source, datasets, seed=None, dependencies=None, artifact_dir=None):
                    return {"status": "succeeded", "exit_code": 0, "stdout": "captured local output\n",
                            "stderr": "", "duration_seconds": 0.01, "image": self.image}
            experiment = run_experiment(directory, {"title": "Offline reproducibility record",
                "source": "print('captured local output')", "dataset_ids": [dataset["dataset_id"]], "seed": 23}, OfflineRunner())
            self.assertEqual(experiment["status"], "succeeded")
            self.assertEqual(experiment["requested_environment"]["network"], "disabled")

            draft = create_draft(directory, {"title": "Sleep and learning report", "document_type": "report",
                "research_question": request.topic, "claim_ids": [reviewed["claim_id"]],
                "design_ids": [design["design_id"]], "experiment_ids": [experiment["experiment_id"]],
                "evaluation_ids": [evaluation["evaluation_id"]]})
            approved = review_draft(directory, draft["draft_id"], "approve", "Reviewed the selected records and placeholders.")
            exported = export_draft(directory, draft["draft_id"], "markdown")
            path, _ = get_export_path(directory, draft["draft_id"], exported["filename"])
            self.assertIn("[C001]", path.read_text(encoding="utf-8"))
            self.assertIn("descriptive", approved["content"].casefold())

            owner, token = create_user(directory, "lead", "Research Lead")
            project = ProjectStore(directory).create({"title": "Sleep research lifecycle"}, owner)
            project = ProjectStore(directory).create_collection(project["project_id"], {"title": "Reviewed lifecycle records",
                "records": [{"record_type": "research_run", "record_id": run.run_id},
                    {"record_type": "paper", "record_id": document["document_id"]},
                    {"record_type": "claim", "record_id": reviewed["claim_id"]},
                    {"record_type": "study_design", "record_id": design["design_id"]},
                    {"record_type": "dataset", "record_id": dataset["dataset_id"]},
                    {"record_type": "evaluation", "record_id": evaluation["evaluation_id"]},
                    {"record_type": "experiment", "record_id": experiment["experiment_id"]},
                    {"record_type": "writing_draft", "record_id": draft["draft_id"]}]}, owner)
            self.assertEqual(len(project["collections"][0]["records"]), 8)
            lifecycle = project_lifecycle(directory, project)
            self.assertEqual(lifecycle["valid_record_count"], 8)
            statuses = {item["stage"]: item["status"] for item in lifecycle["stages"]}
            self.assertEqual(statuses["discovery"], "has_records")
            self.assertEqual(statuses["evidence"], "reviewed")
            self.assertEqual(statuses["study_design"], "approved")
            self.assertEqual(statuses["experiment"], "succeeded")
            self.assertEqual(statuses["writing"], "approved")

            workflows = WorkflowStore(directory, default_agent_registry())
            workflow = workflows.create({"title": "Lifecycle review plan", "provenance": {"research_run_id": run.run_id},
                "tasks": [{"task_id": "plan", "agent": "research_plan", "approval_required": True,
                    "input": {"question": request.topic}}]})
            recovered = WorkflowStore(directory, default_agent_registry()).get(workflow["workflow_id"])
            recovered = WorkflowStore(directory, default_agent_registry()).review(recovered["workflow_id"], "plan", "approve")
            self.assertEqual(recovered["status"], "completed")
            self.assertEqual(get_experiment(directory, experiment["experiment_id"])["dataset_sha256"], [dataset["sha256"]])
            self.assertIn(scientific_quality_report(directory)["status"], {"pass", "review"})
            with tempfile.TemporaryDirectory() as backup_directory:
                archive = Path(backup_directory) / "lifecycle.tar.gz"
                restored_path = Path(backup_directory) / "restored"
                create_backup(directory, archive)
                restore_backup(archive, restored_path)
                restored_actor = {"username": "lead", "admin": False}
                self.assertEqual(ProjectStore(restored_path).get(project["project_id"], restored_actor)["title"], "Sleep research lifecycle")
                self.assertEqual(WorkflowStore(restored_path, default_agent_registry()).get(workflow["workflow_id"])["status"], "completed")
                self.assertEqual(get_experiment(restored_path, experiment["experiment_id"])["status"], "succeeded")
                self.assertEqual(get_export_path(restored_path, draft["draft_id"], exported["filename"])[1]["sha256"], exported["sha256"])
            self.assertTrue(token)


if __name__ == "__main__":
    unittest.main()

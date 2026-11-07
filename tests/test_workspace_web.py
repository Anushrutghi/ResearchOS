import http.client
import base64
import hashlib
from io import BytesIO
import json
import tempfile
from pathlib import Path
from pathlib import Path
from threading import Thread
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from researchos.web import make_handler


class WorkspaceHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.directory.name))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.directory.cleanup()

    def request_json(self, method, path, value=None):
        body = json.dumps(value).encode() if value is not None else None
        self.connection.request(method, path, body=body, headers={"Content-Type": "application/json"} if body else {})
        response = self.connection.getresponse()
        raw = response.read()
        return response.status, json.loads(raw) if raw else None

    def test_workspace_assets_and_nine_release_statuses_are_served(self):
        self.connection.request("GET", "/")
        page = self.connection.getresponse()
        body = page.read().decode()
        self.assertEqual(page.status, 200)
        self.assertIn("ResearchOS", body)
        self.assertIn("/assets/app.js", body)
        status, data = self.request_json("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["versions"]), 9)
        self.assertEqual(data["current_release"], "v1.0")
        self.assertEqual(data["build_progress"]["release"], "v1.0")
        self.assertEqual(next(item for item in data["versions"] if item["release"] == "v0.4")["status"], "complete")
        self.assertEqual(next(item for item in data["versions"] if item["release"] == "v0.5")["status"], "complete")
        self.assertEqual(next(item for item in data["versions"] if item["release"] == "v0.6")["status"], "complete")
        self.assertEqual(next(item for item in data["versions"] if item["release"] == "v0.7")["status"], "complete")
        self.assertEqual(next(item for item in data["versions"] if item["release"] == "v0.8")["status"], "complete")
        self.assertEqual(next(item for item in data["versions"] if item["release"] == "v0.9")["status"], "complete")
        self.assertEqual(next(item for item in data["versions"] if item["release"] == "v1.0")["status"], "complete")
        self.assertIn("current_task", data["build_progress"])
        health_status, health = self.request_json("GET", "/api/health")
        self.assertEqual(health_status, 200)
        self.assertEqual(health["status"], "ok")
        quality_status, quality = self.request_json("GET", "/api/quality")
        self.assertEqual(quality_status, 200)
        self.assertIn("checks", quality)
        self.assertNotIn("score", quality)
        status, note = self.request_json("GET", "/api/versions/v2")
        self.assertEqual(status, 200)
        self.assertIn("Evidence Intelligence Engine", note["content"])
        self.connection.request("GET", "/assets/app.js")
        script = self.connection.getresponse()
        self.assertEqual(script.status, 200)
        script_body = script.read().decode()
        self.assertIn("sendMessage", script_body)
        self.assertIn("renderMethodology", script_body)
        self.assertIn("Suggest questions and hypotheses from reviewed literature", script_body)
        self.connection.request("GET", "/assets/app-extra.css")
        styles = self.connection.getresponse()
        self.assertEqual(styles.status, 200)
        self.assertIn("methodology-form", styles.read().decode())

    def test_chat_send_and_edit_routes_persist_history(self):
        status, chat = self.request_json("POST", "/api/chats", {})
        self.assertEqual(status, 201)
        status, sent = self.request_json("POST", "/api/chat", {"chat_id": chat["id"], "message": "status update"})
        self.assertEqual(status, 200)
        self.assertEqual(sent["chat"]["messages"][0]["role"], "user")
        message_id = sent["user_message"]["id"]
        status, edited = self.request_json("PUT", f"/api/chats/{chat['id']}/messages/{message_id}", {"message": "updated status"})
        self.assertEqual(status, 200)
        self.assertEqual(edited["chat"]["messages"][0]["content"], "updated status")

    def test_configured_ai_chat_uses_recent_messages_and_marks_generated_reply(self):
        status, chat = self.request_json("POST", "/api/chats", {})
        self.assertEqual(status, 201)
        with patch.dict("os.environ", {"RESEARCHOS_AI_PROVIDER": "openai_compatible", "RESEARCHOS_AI_MODEL": "gemini-test",
                                        "RESEARCHOS_API_KEY": "secret", "RESEARCHOS_AI_BASE_URL": "https://example.test/v1"}), \
             patch("researchos.web.OpenAICompatibleChatProvider.respond", return_value="Generated answer") as respond:
            status, sent = self.request_json("POST", "/api/chat", {"chat_id": chat["id"], "message": "Explain this topic"})
            status, edited = self.request_json("PUT", f"/api/chats/{chat['id']}/messages/{sent['user_message']['id']}",
                                               {"message": "Edited research question"})
        self.assertEqual(status, 200)
        self.assertEqual(respond.call_args_list[0].args[0][-1]["content"], "Explain this topic")
        self.assertEqual(sent["assistant_message"]["content"], "Generated answer")
        self.assertEqual(sent["assistant_message"]["source"], "ai")
        self.assertNotIn("secret", json.dumps(sent))
        self.assertEqual(status, 200)
        self.assertEqual(edited["assistant_message"]["content"], "Generated answer")
        self.assertEqual(edited["assistant_message"]["source"], "ai")
        self.assertEqual(respond.call_args.args[0][-1]["content"], "Edited research question")

    def test_agent_workflow_api_persists_proposals_and_requires_review(self):
        status, created = self.request_json("POST", "/api/agent-workflows", {
            "title": "Plan a study", "provenance": {"origin": "researcher_input"},
            "tasks": [{"task_id": "plan", "agent": "research_plan", "input": {"question": "How does sleep affect learning?"}, "approval_required": True}],
        })
        self.assertEqual(status, 201)
        workflow = created["workflow"]
        self.assertEqual(workflow["status"], "awaiting_approval")
        self.assertEqual(workflow["tasks"][0]["provider"], "local")
        self.assertIn("steps", workflow["tasks"][0]["output"])
        workflow_id = workflow["workflow_id"]
        status, saved = self.request_json("GET", f"/api/agent-workflows/{workflow_id}")
        self.assertEqual(status, 200)
        self.assertEqual(saved["workflow"]["tasks"][0]["provenance"], {"origin": "researcher_input"})
        status, approved = self.request_json("POST", f"/api/agent-workflows/{workflow_id}/tasks/plan/review", {"action": "approve"})
        self.assertEqual(status, 200)
        self.assertEqual(approved["workflow"]["status"], "completed")
        self.assertEqual(approved["workflow"]["tasks"][0]["history"][-1]["researcher_action"], "approved")
        status, failed = self.request_json("POST", "/api/agent-workflows", {
            "title": "Invalid agent input", "tasks": [{"task_id": "plan", "agent": "research_plan", "input": {"question": 1}}],
        })
        self.assertEqual(status, 400)

    def test_writing_routes_gate_exports_and_serve_hash_verified_documents(self):
        status, catalog = self.request_json("GET", "/api/writing/catalog")
        self.assertEqual(status, 200)
        status, created = self.request_json("POST", "/api/writing/drafts", {"title": "Local report", "document_type": "report"})
        self.assertEqual(status, 201)
        draft = created["draft"]
        status, blocked = self.request_json("POST", f"/api/writing/drafts/{draft['draft_id']}/export", {"format": "pdf"})
        self.assertEqual(status, 400)
        content = draft["content"] + "\nResearcher-authored addition.\n"
        status, edited = self.request_json("PUT", f"/api/writing/drafts/{draft['draft_id']}", {"content": content})
        self.assertEqual(status, 200)
        self.assertEqual(edited["draft"]["content"], content)
        status, approved = self.request_json("POST", f"/api/writing/drafts/{draft['draft_id']}/review",
                                             {"action": "approve", "note": "Reviewed draft"})
        self.assertEqual(status, 200)
        status, exported = self.request_json("POST", f"/api/writing/drafts/{draft['draft_id']}/export", {"format": "pdf"})
        self.assertEqual(status, 201)
        self.connection.request("GET", f"/api/writing/drafts/{draft['draft_id']}/exports/{exported['export']['filename']}")
        response = self.connection.getresponse()
        data = response.read()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/pdf")
        self.assertTrue(data.startswith(b"%PDF-1.4"))

    def test_sandbox_status_requires_a_valid_matching_local_manifest(self):
        image = "local/research-python@sha256:" + "a" * 64
        manifest = {"schema_version": 1, "image": image, "python_version": "3.12.4", "packages": {"numpy": "2.1.3"}}
        path = Path(self.directory.name) / "sandbox-profile.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch.dict("os.environ", {"RESEARCHOS_SANDBOX_IMAGE": image,
                                       "RESEARCHOS_SANDBOX_MANIFEST": str(path)}):
            status, state = self.request_json("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(state["sandbox_image_configured"])
        self.assertEqual(state["sandbox_profile"]["python_version"], "3.12.4")
        self.assertEqual(state["sandbox_profile"]["packages"], {"numpy": "2.1.3"})
        with patch.dict("os.environ", {"RESEARCHOS_SANDBOX_IMAGE": image,
                                       "RESEARCHOS_SANDBOX_MANIFEST": str(path) + ".missing"}):
            status, state = self.request_json("GET", "/api/state")
        self.assertFalse(state["sandbox_image_configured"])
        self.assertIsNone(state["sandbox_profile"])

    def test_experiment_artifact_download_serves_only_recorded_hash_verified_files(self):
        experiment_id = "12345678-1234-5678-1234-567812345678"
        artifact = b"result,1\n"
        folder = Path(self.directory.name) / "experiments" / experiment_id
        artifact_folder = folder / "artifacts"
        artifact_folder.mkdir(parents=True)
        (artifact_folder / "result.csv").write_bytes(artifact)
        (folder / "record.json").write_text(json.dumps({"artifacts": [{"filename": "result.csv",
            "byte_size": len(artifact), "sha256": hashlib.sha256(artifact).hexdigest()}]}), encoding="utf-8")
        self.connection.request("GET", f"/api/experiments/{experiment_id}/artifacts/result.csv")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), artifact)
        self.connection.request("GET", f"/api/experiments/{experiment_id}/artifacts/missing.csv")
        response = self.connection.getresponse()
        self.assertEqual(response.status, 404)

    def test_dataset_routes_store_hash_provenance_and_experiment_route_fails_closed(self):
        encoded = base64.b64encode(b"model,score\na,0.7\n").decode("ascii")
        status, uploaded = self.request_json("POST", "/api/datasets", {"filename": "scores.csv", "data_base64": encoded})
        self.assertEqual(status, 201)
        dataset = uploaded["dataset"]
        self.assertEqual(dataset["summary"]["row_count"], 1)
        status, listed = self.request_json("GET", "/api/datasets")
        self.assertEqual(status, 200)
        self.assertEqual(listed["datasets"][0]["dataset_id"], dataset["dataset_id"])
        self.connection.request("GET", f"/api/datasets/{dataset['dataset_id']}/file")
        preserved = self.connection.getresponse()
        self.assertEqual(preserved.status, 200)
        self.assertEqual(preserved.read(), b"model,score\na,0.7\n")
        with patch.dict("os.environ", {}, clear=True):
            status, created = self.request_json("POST", "/api/experiments", {
                "title": "Unavailable sandbox", "source": "print('no host fallback')", "dataset_ids": [dataset["dataset_id"]],
            })
        self.assertEqual(status, 201)
        self.assertEqual(created["experiment"]["status"], "failed")
        self.assertIn("no host execution fallback", created["experiment"]["failure"])
        self.assertIsNone(created["experiment"]["execution"])
        status, records = self.request_json("GET", "/api/experiments")
        self.assertEqual(status, 200)
        self.assertEqual(records["experiments"][0]["experiment_id"], created["experiment"]["experiment_id"])
        prediction_data = base64.b64encode(b"truth,estimate\n1,1\n3,2\n5,7\n").decode("ascii")
        status, prediction_upload = self.request_json("POST", "/api/datasets", {
            "filename": "predictions.csv", "data_base64": prediction_data,
        })
        self.assertEqual(status, 201)
        prediction_id = prediction_upload["dataset"]["dataset_id"]
        status, evaluation = self.request_json("POST", f"/api/datasets/{prediction_id}/evaluate", {
            "task": "regression", "actual_column": "truth", "predicted_column": "estimate",
        })
        self.assertEqual(status, 201)
        self.assertAlmostEqual(evaluation["evaluation"]["metrics"]["r2"], 0.375)
        status, saved = self.request_json("GET", "/api/evaluations")
        self.assertEqual(status, 200)
        self.assertEqual(saved["evaluations"][0]["evaluation_id"], evaluation["evaluation"]["evaluation_id"])

    def test_ai_claim_suggestion_route_is_disabled_without_provider_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            status, state = self.request_json("GET", "/api/state")
            self.assertFalse(state["ai_provider_configured"])
            status, result = self.request_json("POST", "/api/claims/extract", {"document_id": "missing", "page_number": 1})
        self.assertEqual(status, 400)
        self.assertIn("Configure RESEARCHOS_AI_PROVIDER", result["error"])

    def test_ai_claim_suggestions_keep_selected_page_provenance(self):
        document = {"document_id": "doc", "pages": [{"page": 2, "text": "Source text", "section_name": "Results"}]}
        candidate = {"claim_text": "A candidate", "evidence_category": "unverified_claim",
                     "extraction_method": "llm:openai_compatible/test", "verification_status": "provisional"}
        with patch.dict("os.environ", {"RESEARCHOS_AI_PROVIDER": "openai_compatible", "RESEARCHOS_AI_MODEL": "test",
                                       "RESEARCHOS_API_KEY": "secret"}), \
             patch("researchos.web.list_documents", return_value=[document]), \
             patch("researchos.web.OpenAICompatibleClaimExtractor.extract", return_value=[candidate]) as extract:
            status, result = self.request_json("POST", "/api/claims/extract", {"document_id": "doc", "page_number": 2})
        self.assertEqual(status, 200)
        self.assertEqual(extract.call_args.args[0], "Source text")
        saved_candidate = result["candidates"][0]
        self.assertEqual(saved_candidate["document_id"], "doc")
        self.assertEqual(saved_candidate["page_number"], 2)
        self.assertEqual(saved_candidate["section_name"], "Results")
        self.assertEqual(saved_candidate["passage_text"], "Source text")
        self.assertEqual(saved_candidate["verification_status"], "provisional")
        self.assertNotIn("secret", json.dumps(result))

    def test_relationship_review_route_records_explicit_researcher_decision(self):
        edge_id = "12345678-1234-5678-1234-567812345678"
        with patch("researchos.web.review_relationship", return_value={"edge_id": edge_id, "status": "rejected"}) as review:
            status, result = self.request_json("PUT", f"/api/relationships/{edge_id}/review",
                                               {"status": "rejected", "reviewer_note": "Different populations"})
        self.assertEqual(status, 200)
        self.assertEqual(result["edge"]["status"], "rejected")
        self.assertEqual(review.call_args.args[1:], (edge_id, "rejected", "Different populations"))

    def test_relationship_analysis_route_returns_only_proposed_contextual_suggestion(self):
        claims = [{"claim_id": "a", "original_text": "First", "source": {"text": "Passage A"}},
                  {"claim_id": "b", "original_text": "Second", "source": {"text": "Passage B"}}]
        suggestion = {"candidate_relation": "uncertain", "confidence": "low", "rationale": "Insufficient context.",
                      "contextual_differences": ["Methods unavailable"], "status": "proposed", "review_required": True}
        config = object()
        with patch("researchos.web.ClaimExtractionConfig.from_environment", return_value=config), \
             patch("researchos.web.load_graph", return_value={"claims": claims}), \
             patch("researchos.web.OpenAICompatibleRelationshipAnalyzer.analyze", return_value=suggestion) as analyze:
            status, result = self.request_json("POST", "/api/relationships/analyze", {"left_claim_id": "a", "right_claim_id": "b"})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "proposed")
        self.assertTrue(result["review_required"])
        self.assertEqual(result["left_claim_id"], "a")
        analyze.assert_called_once_with(*claims)

    def test_methodology_routes_analyze_save_edit_and_approve_designs(self):
        from pypdf import PdfWriter
        from researchos.knowledge.store import add_claim, update_claim_review
        from researchos.workspace import store_pdf
        writer = PdfWriter(); writer.add_blank_page(width=200, height=200)
        fixture = BytesIO(); writer.write(fixture)
        document = store_pdf(self.directory.name, "evidence.pdf", base64.b64encode(fixture.getvalue()).decode())
        claim = add_claim(self.directory.name, document["document_id"], 1, "Source passage", "Reviewed evidence claim")
        claim = update_claim_review(self.directory.name, claim["claim_id"], "reviewed", "Checked source")
        status, analysis = self.request_json("POST", "/api/methodology/analyze", {
            "topic": "Model evaluation", "research_field": "Machine Learning", "research_type": "computational",
        })
        self.assertEqual(status, 200)
        self.assertIsNone(analysis["problem_statement"])
        self.assertTrue(analysis["missing_information"])
        self.assertTrue(any("reviewed against their source" in item for item in analysis["missing_information"]))
        status, grounded = self.request_json("POST", "/api/methodology/analyze", {
            "topic": "Model evaluation", "research_field": "Machine Learning", "research_type": "computational",
            "problem_statement": "Compare two approaches", "objectives": ["Measure quality"],
            "research_questions": ["Which approach performs better?"], "evidence_claim_ids": [claim["claim_id"]],
        })
        self.assertEqual(status, 200)
        self.assertEqual(grounded["literature_context"][0]["verification_status"], "reviewed")
        self.assertEqual(grounded["literature_context"][0]["page_number"], 1)
        status, statistics = self.request_json("POST", "/api/methodology/statistics", {
            "outcome_type": "continuous", "design": "two_independent_groups", "sample_size": 50,
        })
        self.assertEqual(status, 200)
        self.assertEqual(statistics["candidate_methods"], [])
        self.assertTrue(any("independent" in item.lower() for item in statistics["missing_information"]))
        status, survival = self.request_json("POST", "/api/methodology/statistics", {
            "outcome_type": "time_to_event", "design": "two_independent_groups", "sample_size": 80,
            "independent_observations": True,
        })
        self.assertEqual(status, 200)
        self.assertTrue(any("Kaplan-Meier" in item for item in survival["candidate_methods"]))
        self.assertIn("not a selected analysis", survival["warning"])
        status, diagnostics = self.request_json("POST", "/api/methodology/statistics", {
            "outcome_type": "count", "design": "association", "sample_size": 20,
            "dataset_summary": {"sample_size": 21, "mean": 2.0, "variance": 5.5, "zero_fraction": 0.4},
        })
        self.assertEqual(status, 200)
        self.assertTrue(any("conditional mean and variance" in item for item in diagnostics["dataset_diagnostics"]))
        status, result = self.request_json("POST", "/api/methodology/designs", {
            "title": "Benchmark plan", "research_field": "Machine Learning", "research_type": "computational",
            "problem_statement": "Compare models fairly", "objectives": ["Measure predictive quality"],
            "research_questions": ["Which model performs better?"], "methodology": "Benchmarking",
            "methodology_rationale": "It compares models under shared conditions.", "analysis_plan": "Report metrics and intervals.",
            "population_or_material": "Two public datasets", "sample_size": "All eligible records",
            "sampling_method": "Use the dataset's predefined split", "evaluation_metrics": ["F1"],
            "limitations": ["Two datasets limit external validity"], "ethics_notes": "Review dataset licenses; no direct human subjects.",
            "evidence_claim_ids": [claim["claim_id"]],
            "variables": {"independent": ["model"], "dependent": ["F1"], "control": ["dataset"]},
        })
        self.assertEqual(status, 201)
        design = result["design"]
        self.assertEqual(design["status"], "draft")
        status, approved = self.request_json("PUT", f"/api/methodology/designs/{design['design_id']}/approve", {"reviewer_note": "Approved after review"})
        self.assertEqual(status, 200)
        self.assertEqual(approved["design"]["status"], "approved")
        status, listed = self.request_json("GET", "/api/methodology/designs")
        self.assertEqual(status, 200)
        self.assertEqual(listed["designs"][0]["design_id"], design["design_id"])

    def test_question_refinement_route_requires_provider_and_returns_only_proposed_claim_links(self):
        with patch.dict("os.environ", {}, clear=True):
            status, disabled = self.request_json("POST", "/api/methodology/refine", {})
        self.assertEqual(status, 400)
        self.assertIn("Configure an OpenAI-compatible", disabled["error"])
        claim = {"claim_id": "claim-id", "original_text": "Source result", "evidence_category": "direct_experimental_evidence",
                 "verification_status": "reviewed", "source": {"document_id": "doc-id", "page_number": 5,
                 "section_name": "Results", "text": "The source passage."}}
        suggestion = {"questions": [{"text": "A source-linked question?", "rationale": "Uses source claim.", "evidence_claim_ids": ["claim-id"]}],
                      "hypotheses": [], "cautions": [], "status": "proposed", "review_required": True}
        with patch("researchos.web.ClaimExtractionConfig.from_environment", return_value=object()), \
             patch("researchos.web.load_graph", return_value={"claims": [claim]}), \
             patch("researchos.web.OpenAICompatibleQuestionRefiner.refine", return_value=suggestion) as refine:
            status, result = self.request_json("POST", "/api/methodology/refine", {
                "problem_statement": "A specific problem", "research_type": "quantitative", "objectives": ["Assess outcome"],
                "evidence_claim_ids": ["claim-id"],
            })
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "proposed")
        self.assertTrue(result["review_required"])
        self.assertEqual(result["evidence_context"][0]["page_number"], 5)
        refine.assert_called_once_with("A specific problem", "quantitative", ["Assess outcome"], [claim])

    def test_new_research_route_creates_offline_run_and_markdown_brief(self):
        status, result = self.request_json("POST", "/api/research", {"topic": "A local topic", "field": "Science", "search": False})
        self.assertEqual(status, 201)
        self.assertEqual(result["search_completeness"], "skipped")
        self.connection.request("GET", result["report_url"])
        report = self.connection.getresponse()
        self.assertEqual(report.status, 200)
        self.assertIn("A local topic", report.read().decode())


if __name__ == "__main__":
    unittest.main()

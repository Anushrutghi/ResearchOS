import tempfile
import unittest
from pathlib import Path
import os
import subprocess
import sys
from unittest.mock import patch
import json

from researchos.agents.runtime import AgentDefinition, AgentRegistry, AgentResult, ToolRegistry, WorkflowStore, validate_schema
from researchos.agents.providers import OpenAICompatibleAgentProvider
from researchos.knowledge.extraction.claim_extractor import ClaimExtractionConfig
from researchos.agents.defaults import default_agent_registry


OBJECT_INPUT = {"type": "object", "properties": {"text": {"type": "string", "maxLength": 200}},
                "required": ["text"], "additionalProperties": False}
OBJECT_OUTPUT = {"type": "object", "properties": {"plan": {"type": "array", "items": {"type": "string"}}},
                 "required": ["plan"], "additionalProperties": False}


class AgentRuntimeTests(unittest.TestCase):
    def make_store(self, directory):
        agents = AgentRegistry()
        tools = ToolRegistry()
        tools.register("papers.search", lambda args: {"sources": ["doi:example"]})
        def plan(data, context):
            if context.dependencies:
                upstream = next(iter(context.dependencies.values()))
                return {"plan": upstream["plan"] + [f"Continue with {data['text']}"]}
            sources = context.call_tool("papers.search", {"query": data["text"]})["sources"]
            return AgentResult({"plan": [f"Review {data['text']}", *sources]}, provider="test", model="fake-1",
                               usage={"input_tokens": 10, "output_tokens": 5})
        agents.register(AgentDefinition("planner", OBJECT_INPUT, OBJECT_OUTPUT, plan,
                                        frozenset({"papers.search"}), "test", "fake-1"))
        return WorkflowStore(directory, agents, tools)

    def test_schema_validation_rejects_missing_extra_and_wrong_types(self):
        with self.assertRaisesRegex(ValueError, "missing required"):
            validate_schema({}, OBJECT_INPUT)
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            validate_schema({"text": "ok", "secret": "no"}, OBJECT_INPUT)
        with self.assertRaisesRegex(ValueError, "JSON type"):
            validate_schema({"text": 4}, OBJECT_INPUT)

    def test_study_design_agent_returns_conditional_checks_not_approved_design(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(directory, default_agent_registry())
            workflow = store.create({"title": "Study design checks", "tasks": [{"task_id": "check",
                "agent": "study_design_guidance", "approval_required": True,
                "input": {"topic": "Intervention outcomes", "research_field": "Medicine",
                    "research_type": "quantitative", "problem_statement": "Compare two treatments.",
                    "objectives": ["Estimate outcomes"], "research_questions": ["Which treatment differs?"]}}]})
            task = workflow["tasks"][0]
            self.assertEqual(task["status"], "awaiting_approval")
            self.assertTrue(task["output"]["review_required"])
            self.assertTrue(any("ethics" in item.casefold() for item in task["output"]["cautions"]))
            self.assertGreater(len(task["output"]["method_prompts"]), 0)

    def test_durable_graph_enforces_permissions_provenance_and_researcher_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            workflow = store.create({"title": "Research plan", "provenance": {"source_ids": ["paper-1"]},
                "tasks": [{"task_id": "plan", "agent": "planner", "input": {"text": "federated learning"},
                           "approval_required": True},
                          {"task_id": "next", "agent": "planner", "input": {"text": "review"},
                           "depends_on": ["plan"]}]})
            self.assertEqual(workflow["status"], "awaiting_approval")
            self.assertEqual(workflow["tasks"][0]["status"], "awaiting_approval")
            self.assertEqual(workflow["tasks"][1]["status"], "pending")
            self.assertEqual(workflow["tasks"][0]["provider"], "test")
            self.assertEqual(workflow["tasks"][0]["usage"]["input_tokens"], 10)
            self.assertEqual(workflow["tasks"][0]["provenance"], {"source_ids": ["paper-1"]})
            workflow = store.review(workflow["workflow_id"], "plan", "edit", {"plan": ["researcher edit"]})
            self.assertEqual(workflow["tasks"][0]["output"], {"plan": ["researcher edit"]})
            self.assertEqual(workflow["tasks"][1]["status"], "completed")
            self.assertEqual(workflow["tasks"][1]["output"]["plan"][-1], "Continue with review")
            self.assertEqual(workflow["status"], "completed")
            reopened = self.make_store(directory).get(workflow["workflow_id"])
            self.assertEqual(reopened, workflow)
            self.assertEqual(reopened["tasks"][0]["tool_calls"][0]["tool"], "papers.search")

    def test_agent_cannot_call_unapproved_tools_and_failure_is_durable_and_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            agents = AgentRegistry()
            tools = ToolRegistry()
            tools.register("papers.search", lambda args: [])
            agents.register(AgentDefinition("blocked", OBJECT_INPUT, OBJECT_OUTPUT,
                lambda data, context: context.call_tool("papers.search", {})))
            store = WorkflowStore(directory, agents, tools)
            failed = store.create({"title": "Denied tool", "tasks": [{"task_id": "t", "agent": "blocked",
                                                                         "input": {"text": "x"}}]})
            self.assertEqual(failed["status"], "failed")
            self.assertIn("not allowed", failed["tasks"][0]["failure"])
            self.assertEqual(store.get(failed["workflow_id"])["tasks"][0]["attempts"], 1)
            self.assertEqual(failed["tasks"][0]["tool_calls"][0]["status"], "denied")

    def test_resume_recovers_interrupted_task_and_retries_with_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            attempts = {"count": 0}
            agents = AgentRegistry()
            def sometimes(data, context):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise RuntimeError("temporary")
                return {"plan": ["ready"]}
            agents.register(AgentDefinition("retry", OBJECT_INPUT, OBJECT_OUTPUT, sometimes))
            store = WorkflowStore(directory, agents)
            failed = store.create({"title": "Retry", "tasks": [{"task_id": "t", "agent": "retry", "input": {"text": "x"}}]})
            self.assertEqual(failed["status"], "failed")
            complete = store.run(failed["workflow_id"])
            self.assertEqual(complete["status"], "completed")
            self.assertEqual(complete["tasks"][0]["attempts"], 2)

            interrupted = store.create({"title": "Interrupted", "tasks": [{"task_id": "t", "agent": "retry", "input": {"text": "y"}}]})
            self.assertEqual(interrupted["status"], "completed")
            folder = Path(directory) / "agent_workflows" / interrupted["workflow_id"] / "state.json"
            state = store.get(interrupted["workflow_id"])
            state["status"] = "pending"
            state["tasks"][0]["status"] = "running"
            folder.write_text(__import__("json").dumps(state), encoding="utf-8")
            resumed = store.run(interrupted["workflow_id"])
            self.assertEqual(resumed["tasks"][0]["attempts"], 2)
            self.assertTrue(any(event["event"] == "interrupted" for event in resumed["events"]))

    def test_task_graph_requires_prior_dependencies_and_known_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            with self.assertRaisesRegex(ValueError, "earlier task IDs"):
                store.create({"title": "Bad graph", "tasks": [{"task_id": "a", "agent": "planner",
                    "input": {"text": "x"}, "depends_on": ["b"]}]})
            with self.assertRaisesRegex(ValueError, "Unknown agent"):
                store.create({"title": "Unknown", "tasks": [{"task_id": "a", "agent": "missing", "input": {}}]})

    def test_openai_compatible_provider_parses_structured_output_and_records_usage(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                return json.dumps({"choices": [{"message": {"content": json.dumps({"objective": "test", "steps": ["review"]})}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}}).encode()
        config = ClaimExtractionConfig("openai_compatible", "test-model", "secret", "https://example.invalid/v1")
        provider = OpenAICompatibleAgentProvider(config)
        with patch("researchos.agents.providers.urlopen", return_value=Response()) as request:
            result = provider.invoke({"question": "untrusted question"})
        self.assertEqual(result["output"]["objective"], "test")
        self.assertEqual(result["usage"]["total_tokens"], 20)
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body["model"], "test-model")
        self.assertIn("untrusted data", body["messages"][0]["content"])

    def test_retry_limit_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            count = {"calls": 0}
            agents = AgentRegistry()
            def failing(data, context):
                count["calls"] += 1
                raise RuntimeError("still unavailable")
            agents.register(AgentDefinition("failing", OBJECT_INPUT, OBJECT_OUTPUT, failing))
            store = WorkflowStore(directory, agents)
            workflow = store.create({"title": "Bound retries", "tasks": [{"task_id": "t", "agent": "failing", "input": {"text": "x"}}]})
            for _ in range(8):
                workflow = store.run(workflow["workflow_id"])
            self.assertEqual(count["calls"], 4)
            self.assertEqual(workflow["tasks"][0]["attempts"], 4)
            self.assertEqual(len(workflow["tasks"][0]["history"]), 4)
            self.assertIn("still unavailable", workflow["tasks"][0]["history"][0]["failure"])

    def test_researcher_can_request_revision_then_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            workflow = store.create({"title": "Review actions", "tasks": [{"task_id": "plan", "agent": "planner",
                "input": {"text": "first question"}, "approval_required": True}]})
            revised = store.review(workflow["workflow_id"], "plan", "revise", {"text": "revised question"})
            self.assertEqual(revised["tasks"][0]["input"]["text"], "revised question")
            self.assertEqual(revised["tasks"][0]["status"], "awaiting_approval")
            self.assertEqual(revised["tasks"][0]["attempts"], 2)
            rejected = store.review(workflow["workflow_id"], "plan", "reject")
            self.assertEqual(rejected["status"], "rejected")
            self.assertEqual(rejected["tasks"][0]["status"], "rejected")

    def test_workflow_is_reopened_and_resumed_in_a_fresh_python_process(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(directory, default_agent_registry())
            workflow = store.create({"title": "Process restart", "tasks": [{"task_id": "plan",
                "agent": "research_plan", "input": {"question": "test restart recovery"}, "approval_required": True}]})
            state_path = Path(directory) / "agent_workflows" / workflow["workflow_id"] / "state.json"
            state = json.loads(state_path.read_text())
            state["status"] = "pending"
            state["tasks"][0]["status"] = "running"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            script = ("import json,sys; from researchos.agents.defaults import default_agent_registry; "
                      "from researchos.agents.runtime import WorkflowStore; "
                      "s=WorkflowStore(sys.argv[1],default_agent_registry()); "
                      "print(json.dumps(s.run(sys.argv[2])))")
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + environment.get("PYTHONPATH", "")
            result = subprocess.run([sys.executable, "-c", script, directory, workflow["workflow_id"]],
                                   capture_output=True, check=False, env=environment, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            recovered = json.loads(result.stdout)
            self.assertEqual(recovered["status"], "awaiting_approval")
            self.assertEqual(recovered["tasks"][0]["attempts"], 2)
            self.assertTrue(any(event["event"] == "interrupted" for event in recovered["events"]))


if __name__ == "__main__":
    unittest.main()

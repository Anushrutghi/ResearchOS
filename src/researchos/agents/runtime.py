"""Typed, durable agent workflows with explicit tool permissions and review gates."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
import uuid
from functools import wraps


MAX_WORKFLOW_TASKS = 100
MAX_RETRIES = 3


def _serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_schema(value: object, schema: dict, path: str = "$ ") -> None:
    """Validate the deliberately small JSON Schema subset used by agents."""
    if not isinstance(schema, dict):
        raise ValueError(f"Schema for {path} must be an object.")
    expected = schema.get("type")
    type_checks = {"object": lambda item: isinstance(item, dict),
                   "array": lambda item: isinstance(item, list),
                   "string": lambda item: isinstance(item, str),
                   "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
                   "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
                   "boolean": lambda item: isinstance(item, bool), "null": lambda item: item is None}
    if expected not in type_checks or not type_checks[expected](value):
        raise ValueError(f"{path} must have JSON type {expected!r}.")
    if expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ValueError(f"Object schema at {path} is malformed.")
        missing = set(required) - value.keys()
        if missing:
            raise ValueError(f"{path} is missing required fields: {', '.join(sorted(missing))}.")
        if schema.get("additionalProperties", False) is False and set(value) - properties.keys():
            raise ValueError(f"{path} contains unsupported fields.")
        for key, item in value.items():
            if key in properties:
                validate_schema(item, properties[key], f"{path}.{key}")
    elif expected == "array":
        if len(value) > schema.get("maxItems", 10_000):
            raise ValueError(f"{path} exceeds its item limit.")
        for index, item in enumerate(value):
            validate_schema(item, schema.get("items", {}), f"{path}[{index}]")
    elif expected == "string":
        if len(value) > schema.get("maxLength", 100_000):
            raise ValueError(f"{path} exceeds its length limit.")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{path} is not an allowed value.")
    elif expected == "number":
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} is below its minimum.")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} exceeds its maximum.")


@dataclass(frozen=True)
class AgentResult:
    output: dict
    provider: str = "local"
    model: str = "deterministic"
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    input_schema: dict
    output_schema: dict
    handler: object
    permitted_tools: frozenset[str] = frozenset()
    provider: str = "local"
    model: str = "deterministic"


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, name: str, handler) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", name) or name in self._tools:
            raise ValueError("Tool names must be unique normalized identifiers.")
        self._tools[name] = handler

    def invoke(self, name: str, arguments: dict):
        try:
            handler = self._tools[name]
        except KeyError as exc:
            raise PermissionError(f"Tool {name!r} is not registered.") from exc
        return handler(arguments)


class AgentContext:
    """The only tool access passed to an agent handler."""
    def __init__(self, definition: AgentDefinition, tools: ToolRegistry, dependency_outputs: dict | None = None):
        self.provider = definition.provider
        self.model = definition.model
        self._permitted = definition.permitted_tools
        self._tools = tools
        self.tool_calls = []
        self.dependencies = dependency_outputs or {}

    def call_tool(self, name: str, arguments: dict):
        if name not in self._permitted:
            self.tool_calls.append({"tool": name, "arguments": arguments, "status": "denied"})
            raise PermissionError(f"Agent {name!r} is not allowed to use this tool.")
        call = {"tool": name, "arguments": arguments, "status": "running"}
        self.tool_calls.append(call)
        try:
            result = self._tools.invoke(name, arguments)
            call["status"] = "completed"
            return result
        except Exception as exc:
            call["status"] = "failed"
            call["failure"] = f"{type(exc).__name__}: {str(exc)}"[:1000]
            raise


class AgentRegistry:
    def __init__(self):
        self._agents = {}

    def register(self, definition: AgentDefinition) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", definition.name) or definition.name in self._agents:
            raise ValueError("Agent names must be unique normalized identifiers.")
        self._agents[definition.name] = definition

    def get(self, name: str) -> AgentDefinition:
        if name not in self._agents:
            raise ValueError(f"Unknown agent: {name}.")
        return self._agents[name]


class WorkflowStore:
    def __init__(self, output_dir: str | Path, agents: AgentRegistry, tools: ToolRegistry | None = None):
        self.root = Path(output_dir) / "agent_workflows"
        self.agents = agents
        self.tools = tools or ToolRegistry()
        self._lock = threading.RLock()

    @_serialized
    def create(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) - {"title", "tasks", "provenance"}:
            raise ValueError("Workflow request contains unsupported fields.")
        title, tasks = payload.get("title"), payload.get("tasks")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("Workflow title is required and limited to 200 characters.")
        if not isinstance(tasks, list) or not tasks or len(tasks) > MAX_WORKFLOW_TASKS:
            raise ValueError("A workflow must contain 1 to 100 tasks.")
        provenance = payload.get("provenance", {})
        if not isinstance(provenance, dict):
            raise ValueError("Workflow provenance must be a JSON object.")
        records, seen = [], set()
        for task in tasks:
            if not isinstance(task, dict) or set(task) - {"task_id", "agent", "input", "depends_on", "approval_required"}:
                raise ValueError("Workflow task contains unsupported fields.")
            task_id, agent_name = task.get("task_id"), task.get("agent")
            if not isinstance(task_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", task_id) or task_id in seen:
                raise ValueError("Workflow task IDs must be unique safe identifiers.")
            if not isinstance(agent_name, str):
                raise ValueError("Workflow task must name a registered agent.")
            definition = self.agents.get(agent_name)
            dependencies = task.get("depends_on", [])
            if not isinstance(dependencies, list) or any(item not in seen for item in dependencies):
                raise ValueError("Tasks must depend only on earlier task IDs in a directed acyclic order.")
            if not isinstance(task.get("approval_required", False), bool):
                raise ValueError("approval_required must be a boolean.")
            validate_schema(task.get("input"), definition.input_schema)
            records.append({"task_id": task_id, "agent": agent_name, "input": task["input"],
                "depends_on": dependencies, "approval_required": task.get("approval_required", False),
                "status": "pending", "attempts": 0, "output": None, "failure": None,
                "provenance": provenance, "provider": definition.provider, "model": definition.model,
                "usage": {}, "tool_calls": [], "history": []})
            seen.add(task_id)
        workflow_id = str(uuid.uuid4())
        workflow = {"workflow_id": workflow_id, "title": title.strip(), "status": "pending",
                    "created_at": _now(), "updated_at": _now(), "provenance": provenance,
                    "tasks": records, "events": []}
        self._write(workflow)
        return self.run(workflow_id)

    @_serialized
    def get(self, workflow_id: str) -> dict:
        if not isinstance(workflow_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", workflow_id):
            raise ValueError("Invalid workflow identifier.")
        path = self.root / workflow_id / "state.json"
        if not path.is_file():
            raise FileNotFoundError("Workflow not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    @_serialized
    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        return sorted((self.get(item.name) for item in self.root.iterdir() if item.is_dir()),
                      key=lambda item: item["created_at"], reverse=True)

    @_serialized
    def run(self, workflow_id: str) -> dict:
        workflow = self.get(workflow_id)
        if workflow["status"] in {"completed", "rejected"}:
            return workflow
        for task in workflow["tasks"]:
            if task["status"] == "running":
                task["status"] = "failed"
                task["failure"] = "Worker stopped before recording a result; safe to resume."
                workflow["events"].append({"task_id": task["task_id"], "event": "interrupted",
                                           "recorded_at": _now()})
        for task in workflow["tasks"]:
            if task["status"] not in {"pending", "failed", "revision_requested", "blocked"}:
                continue
            dependency_states = {item["task_id"]: item["status"] for item in workflow["tasks"]}
            if any(dependency_states[item] not in {"approved", "completed"} for item in task["depends_on"]):
                if task["depends_on"]:
                    task["status"] = "blocked"
                continue
            if task["attempts"] >= MAX_RETRIES + 1:
                task["status"] = "failed"
                task["failure"] = task["failure"] or "Retry limit reached."
                break
            definition = self.agents.get(task["agent"])
            dependency_outputs = {dependency: next(item["output"] for item in workflow["tasks"]
                                                   if item["task_id"] == dependency)
                                  for dependency in task["depends_on"]}
            context = AgentContext(definition, self.tools, dependency_outputs)
            task["status"] = "running"
            task["attempts"] += 1
            task["started_at"] = _now()
            self._write(workflow)
            result = None
            try:
                validate_schema(task["input"], definition.input_schema)
                result = definition.handler(task["input"], context)
                result = result if isinstance(result, AgentResult) else AgentResult(result, definition.provider, definition.model)
                validate_schema(result.output, definition.output_schema)
                if len(json.dumps(result.output, ensure_ascii=False).encode("utf-8")) > 1_000_000:
                    raise ValueError("Agent output exceeds the 1 MiB persistence limit.")
                if not isinstance(result.provider, str) or not isinstance(result.model, str) or \
                        len(result.provider) > 100 or len(result.model) > 200 or not isinstance(result.usage, dict) or \
                        len(json.dumps(result.usage, ensure_ascii=False).encode("utf-8")) > 20_000:
                    raise ValueError("Agent provider, model, or usage metadata is invalid or too large.")
                task["history"].append({"attempt": task["attempts"], "output": result.output,
                    "provider": result.provider, "model": result.model, "usage": result.usage,
                    "tool_calls": context.tool_calls, "recorded_at": _now()})
                task["output"] = result.output
                task["failure"] = None
                task["provider"], task["model"], task["usage"] = result.provider, result.model, result.usage
                task["tool_calls"] = context.tool_calls
                task["status"] = "awaiting_approval" if task["approval_required"] else "completed"
                workflow["events"].append({"task_id": task["task_id"], "event": "output_proposed",
                                           "recorded_at": _now()})
                self._write(workflow)
                if task["status"] == "awaiting_approval":
                    break
            except Exception as exc:
                task["status"] = "failed"
                task["failure"] = f"{type(exc).__name__}: {str(exc)}"[:2000]
                task["tool_calls"] = context.tool_calls
                task["history"].append({"attempt": task["attempts"], "failure": task["failure"],
                    "provider": result.provider if isinstance(result, AgentResult) else definition.provider,
                    "model": result.model if isinstance(result, AgentResult) else definition.model,
                    "usage": result.usage if isinstance(result, AgentResult) else {},
                    "tool_calls": context.tool_calls, "recorded_at": _now()})
                workflow["events"].append({"task_id": task["task_id"], "event": "failed",
                                           "recorded_at": _now()})
                self._write(workflow)
                break
        states = [task["status"] for task in workflow["tasks"]]
        if "awaiting_approval" in states:
            workflow["status"] = "awaiting_approval"
        elif "failed" in states:
            workflow["status"] = "failed"
        elif "rejected" in states:
            workflow["status"] = "rejected"
        elif all(state in {"approved", "completed"} for state in states):
            workflow["status"] = "completed"
        else:
            workflow["status"] = "pending"
        self._write(workflow)
        return workflow

    @_serialized
    def review(self, workflow_id: str, task_id: str, action: str, edited_output: dict | None = None) -> dict:
        workflow = self.get(workflow_id)
        task = next((item for item in workflow["tasks"] if item["task_id"] == task_id), None)
        if task is None or task["status"] != "awaiting_approval":
            raise ValueError("Task is not waiting for researcher review.")
        if action == "approve":
            task["status"] = "approved"
            task["history"].append({"researcher_action": "approved", "recorded_at": _now()})
        elif action == "reject":
            task["status"] = "rejected"
            task["history"].append({"researcher_action": "rejected", "recorded_at": _now()})
        elif action == "edit":
            definition = self.agents.get(task["agent"])
            validate_schema(edited_output, definition.output_schema)
            task["history"].append({"researcher_action": "edited", "output": edited_output, "recorded_at": _now()})
            task["output"] = edited_output
            task["status"] = "approved"
        elif action == "revise":
            definition = self.agents.get(task["agent"])
            if edited_output is not None:
                validate_schema(edited_output, definition.input_schema)
                if len(json.dumps(edited_output, ensure_ascii=False).encode("utf-8")) > 1_000_000:
                    raise ValueError("Revised task input exceeds the 1 MiB limit.")
                task["input"] = edited_output
            task["history"].append({"researcher_action": "revision_requested", "recorded_at": _now()})
            task["status"] = "revision_requested"
            task["output"] = None
        else:
            raise ValueError("Review action must be approve, reject, edit, or revise.")
        workflow["events"].append({"task_id": task_id, "event": f"researcher_{action}", "recorded_at": _now()})
        self._write(workflow)
        return self.run(workflow_id)

    def _write(self, workflow: dict) -> None:
        workflow["updated_at"] = _now()
        folder = self.root / workflow["workflow_id"]
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / "state.json.tmp"
        temporary.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(folder / "state.json")

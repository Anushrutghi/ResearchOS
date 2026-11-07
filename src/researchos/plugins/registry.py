"""Declarative plugin contracts bound to a small set of trusted host adapters."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import threading

from researchos.agents.runtime import validate_schema
from researchos.research.providers.crossref import CrossrefProvider
from researchos.research.providers.openalex import OpenAlexProvider


PLUGIN_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")
API_VERSION = "1"
PERMISSIONS = {"external.network.read", "literature.search", "projects.read", "collections.read", "collections.write"}
HANDLERS = {
    "literature.crossref.search": {"permissions": {"external.network.read", "literature.search"}, "function": CrossrefProvider},
    "literature.openalex.search": {"permissions": {"external.network.read", "literature.search"}, "function": OpenAlexProvider},
}
SEARCH_INPUT = {"type": "object", "properties": {"query": {"type": "string", "maxLength": 500},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["query", "limit"], "additionalProperties": False}
SEARCH_OUTPUT = {"type": "object", "properties": {"works": {"type": "array", "maxItems": 100,
    "items": {"type": "object", "properties": {"work_id": {"type": "string", "maxLength": 2000},
        "title": {"type": "string", "maxLength": 2000}}, "required": ["work_id", "title"], "additionalProperties": True}},
    "provider": {"type": "string", "maxLength": 40}, "query": {"type": "string", "maxLength": 500}},
    "required": ["works", "provider", "query"], "additionalProperties": False}
_LOCK = threading.RLock()


def _now(): return datetime.now(timezone.utc).isoformat()


class PluginRegistry:
    def __init__(self, output_dir):
        self.folder = Path(output_dir) / "plugins"

    def list(self) -> list[dict]:
        if not self.folder.exists(): return []
        result = []
        for path in self.folder.glob("*/plugin.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                item.pop("manifest", None)
                result.append(item)
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda item: item.get("plugin_id", ""))

    def get(self, plugin_id: str) -> dict:
        if not isinstance(plugin_id, str) or not PLUGIN_ID.fullmatch(plugin_id):
            raise ValueError("Invalid plugin identifier.")
        path = self.folder / plugin_id / "plugin.json"
        if not path.is_file(): raise FileNotFoundError("Plugin is not installed.")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("plugin_id") != plugin_id: raise RuntimeError("Plugin record identifier does not match its path.")
        return record

    def install(self, manifest: dict, actor: str) -> dict:
        clean = validate_manifest(manifest)
        with _LOCK:
            for dependency, version in clean["dependencies"].items():
                installed = self.get(dependency)
                if installed.get("version") != version:
                    raise ValueError(f"Plugin dependency {dependency}=={version} must be installed first.")
            path = self.folder / clean["plugin_id"] / "plugin.json"
            if path.exists(): raise ValueError("Plugin ID is already installed; choose a new versioned plugin ID or uninstall it first.")
            record = {"plugin_id": clean["plugin_id"], "name": clean["name"], "version": clean["version"],
                "api_version": clean["api_version"], "dependencies": clean["dependencies"],
                "permissions": clean["permissions"], "capabilities": clean["capabilities"],
                "manifest": clean, "status": "disabled", "installed_by": actor,
                "installed_at": _now(), "events": []}
            record["events"].append({"action": "installed_disabled", "actor": actor, "at": record["installed_at"]})
            self._write(record)
            return record

    def set_enabled(self, plugin_id: str, enabled: bool, actor: str) -> dict:
        if not isinstance(enabled, bool): raise ValueError("Plugin enabled flag must be boolean.")
        with _LOCK:
            record = self.get(plugin_id)
            if enabled:
                for dependency, version in record["dependencies"].items():
                    dep = self.get(dependency)
                    if dep.get("status") != "enabled" or dep.get("version") != version:
                        raise ValueError(f"Plugin dependency {dependency}=={version} must be enabled first.")
            record["status"] = "enabled" if enabled else "disabled"
            record["events"].append({"action": record["status"], "actor": actor, "at": _now()})
            self._write(record)
            return record

    def run(self, plugin_id: str, capability_id: str, payload: dict) -> dict:
        record = self.get(plugin_id)
        if record.get("status") != "enabled": raise PermissionError("Plugin is disabled.")
        capability = next((item for item in record["capabilities"] if item["capability_id"] == capability_id), None)
        if not capability: raise FileNotFoundError("Plugin capability is not installed.")
        handler_info = HANDLERS[capability["handler"]]
        if not handler_info["permissions"].issubset(set(record["permissions"])):
            raise PermissionError("Plugin manifest does not grant the permissions required by this host capability.")
        validate_schema(payload, capability["input_schema"])
        if capability["handler"] == "literature.crossref.search": provider_name, provider = "Crossref", CrossrefProvider()
        elif capability["handler"] == "literature.openalex.search": provider_name, provider = "OpenAlex", OpenAlexProvider()
        else: raise PermissionError("No trusted adapter is registered for this capability.")
        papers = provider.search(payload["query"], payload["limit"])
        result = {"works": [paper.to_dict() for paper in papers], "provider": provider_name,
                  "query": payload["query"]}
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 2_000_000:
            raise RuntimeError("Plugin adapter output exceeds the 2 MB response limit.")
        validate_schema(result, capability["output_schema"])
        return result

    def _write(self, record: dict) -> None:
        folder = self.folder / record["plugin_id"]
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / "plugin.json.tmp"
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(folder / "plugin.json")


def validate_manifest(manifest: object) -> dict:
    expected = {"schema_version", "plugin_id", "name", "version", "api_version", "dependencies", "permissions", "capabilities"}
    if not isinstance(manifest, dict) or set(manifest) != expected:
        raise ValueError("Plugin manifest must contain exactly the v1 contract fields.")
    if isinstance(manifest["schema_version"], bool) or manifest["schema_version"] != 1 or not isinstance(manifest["plugin_id"], str) or not PLUGIN_ID.fullmatch(manifest["plugin_id"]):
        raise ValueError("Plugin manifest has an unsupported schema version or invalid plugin_id.")
    if not isinstance(manifest["name"], str) or not manifest["name"].strip() or len(manifest["name"]) > 120:
        raise ValueError("Plugin name is required and limited to 120 characters.")
    if not isinstance(manifest["version"], str) or not SEMVER.fullmatch(manifest["version"]):
        raise ValueError("Plugin version must use semantic version syntax.")
    if manifest["api_version"] != API_VERSION:
        raise ValueError(f"Plugin API version must be {API_VERSION!r}.")
    dependencies = manifest["dependencies"]
    if not isinstance(dependencies, dict) or len(dependencies) > 30:
        raise ValueError("Plugin dependencies must be an object with up to 30 exact versions.")
    for dependency, version in dependencies.items():
        if not isinstance(dependency, str) or dependency == manifest["plugin_id"] or not PLUGIN_ID.fullmatch(dependency) or not isinstance(version, str) or not SEMVER.fullmatch(version):
            raise ValueError("Plugin dependencies must use other valid plugin IDs and exact semantic versions.")
    permissions = manifest["permissions"]
    if not isinstance(permissions, list) or len(permissions) > len(PERMISSIONS) or \
            any(not isinstance(item, str) for item in permissions) or len(set(permissions)) != len(permissions) or set(permissions) - PERMISSIONS:
        raise ValueError("Plugin manifest contains invalid, duplicate, or unsupported permissions.")
    capabilities = manifest["capabilities"]
    if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 30:
        raise ValueError("Plugin manifest must define 1 to 30 capabilities.")
    ids = set()
    for capability in capabilities:
        fields = {"capability_id", "handler", "input_schema", "output_schema"}
        if not isinstance(capability, dict) or set(capability) != fields:
            raise ValueError("Each plugin capability must declare an ID, trusted handler, and input/output schemas.")
        capability_id, handler = capability["capability_id"], capability["handler"]
        if not isinstance(capability_id, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", capability_id) or capability_id in ids:
            raise ValueError("Plugin capability IDs must be unique normalized identifiers.")
        ids.add(capability_id)
        if not isinstance(handler, str) or handler not in HANDLERS:
            raise ValueError("Plugin handler is not registered by the trusted ResearchOS host.")
        for schema in (capability["input_schema"], capability["output_schema"]):
            _validate_schema_definition(schema)
        if not capability["input_schema"].get("required"):
            raise ValueError("Plugin capability input schema must specify required fields.")
    return json.loads(json.dumps(manifest))


def _validate_schema_definition(schema: object, depth: int = 0) -> None:
    if depth > 12 or not isinstance(schema, dict):
        raise ValueError("Plugin JSON schemas must be objects nested at most 12 levels.")
    if schema.get("type") not in {"object", "array", "string", "integer", "number", "boolean", "null"}:
        raise ValueError("Plugin JSON schema uses an unsupported type.")
    allowed = {"object": {"type", "properties", "required", "additionalProperties"},
               "array": {"type", "items", "maxItems"}, "string": {"type", "maxLength"},
               "integer": {"type", "minimum", "maximum"}, "number": {"type", "minimum", "maximum"},
               "boolean": {"type"}, "null": {"type"}}[schema["type"]]
    if set(schema) - allowed:
        raise ValueError("Plugin JSON schema contains unsupported keywords.")
    if schema["type"] == "object":
        properties, required = schema.get("properties", {}), schema.get("required", [])
        if not isinstance(properties, dict) or len(properties) > 100 or not isinstance(required, list) or \
                any(not isinstance(key, str) for key in properties) or any(not isinstance(item, str) or item not in properties for item in required) or \
                not isinstance(schema.get("additionalProperties", False), bool):
            raise ValueError("Plugin object schema has invalid properties, required fields, or additionalProperties.")
        for child in properties.values(): _validate_schema_definition(child, depth+1)
    elif schema["type"] == "array":
        maximum = schema.get("maxItems", 100)
        if not isinstance(maximum, int) or isinstance(maximum, bool) or not 0 <= maximum <= 10_000:
            raise ValueError("Plugin array schemas need a bounded maxItems value.")
        _validate_schema_definition(schema.get("items"), depth+1)
    elif schema["type"] == "string":
        maximum = schema.get("maxLength", 100_000)
        if not isinstance(maximum, int) or isinstance(maximum, bool) or not 0 <= maximum <= 1_000_000:
            raise ValueError("Plugin string schemas need a bounded maxLength value.")
    elif schema["type"] in {"integer", "number"}:
        for key in ("minimum", "maximum"):
            value = schema.get(key)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)):
                raise ValueError("Plugin numeric bounds must be numbers.")

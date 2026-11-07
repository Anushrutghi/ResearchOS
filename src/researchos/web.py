"""Local browser interface for ResearchOS, built with the standard library."""

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from researchos.core.orchestrator import run_research
from researchos.core.request import create_research_request
from researchos.knowledge.store import add_claim, add_relationship, load_graph, review_relationship, search_knowledge, update_claim_review
from researchos.knowledge.extraction.claim_extractor import ClaimExtractionConfig, OpenAICompatibleClaimExtractor
from researchos.knowledge.extraction.relationship_analyzer import OpenAICompatibleRelationshipAnalyzer
from researchos.methodology.analyzer import analyze_research_problem, recommend_statistical_methods
from researchos.methodology.question_refiner import OpenAICompatibleQuestionRefiner
from researchos.methodology.store import approve_study_design, get_study_design, list_study_designs, save_study_design
from researchos.experiments.datasets import dataset_path, get_dataset, import_dataset, list_datasets
from researchos.experiments.analysis import evaluate_prediction_columns, list_evaluations
from researchos.experiments.sandbox import DockerSandboxRunner, load_sandbox_profile
from researchos.experiments.store import experiment_artifact_path, get_experiment, list_experiments, run_experiment
from researchos.agents.defaults import default_agent_registry
from researchos.agents.chat import OpenAICompatibleChatProvider
from researchos.agents.runtime import WorkflowStore
from researchos.writing.store import (create_draft, edit_draft, export_draft, get_draft,
    get_export_path, list_drafts, review_draft, writing_catalog)
from researchos.workspace import (
    PROJECT_ROOT, VERSION_STATUS, append_chat_message, create_chat, edit_chat_message, get_build_progress, get_chat, get_version_note,
    list_chats, list_documents, store_pdf, workspace_reply,
)
from researchos.security.auth import authenticate
from researchos.projects.store import ProjectStore, page_items
from researchos.projects.lifecycle import project_lifecycle
from researchos.plugins.registry import PluginRegistry
from researchos.maintenance import scientific_quality_report

STATIC_DIR = Path(__file__).with_name("static")


def _configured_sandbox_runner() -> DockerSandboxRunner | None:
    """Load the operator-provided profile and require it to match the pinned image."""
    image = os.environ.get("RESEARCHOS_SANDBOX_IMAGE", "")
    manifest = os.environ.get("RESEARCHOS_SANDBOX_MANIFEST", "")
    if not image or not manifest:
        return None
    try:
        profile = load_sandbox_profile(manifest, image)
        return DockerSandboxRunner(image, profile=profile)
    except (OSError, ValueError):
        return None


STYLE = """
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2c;--line:#27344c;--text:#e9effb;--muted:#9aabc4;--accent:#8dc5ff;--green:#8de0bd}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top,#172846 0,#0b1020 48rem);color:var(--text);font:16px/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}
.wrap{width:min(1040px,calc(100% - 36px));margin:0 auto;padding:56px 0 80px}.brand{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700}
h1{font-size:clamp(36px,6vw,58px);line-height:1.05;letter-spacing:-.045em;margin:18px 0 14px}.intro{max-width:650px;color:var(--muted);font-size:18px;margin:0 0 34px}
.panel{background:rgba(18,26,44,.92);border:1px solid var(--line);border-radius:18px;padding:26px;box-shadow:0 20px 70px #0003}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.field{display:flex;flex-direction:column;gap:8px}.wide{grid-column:1/-1}label{font-weight:650;font-size:14px;color:#cfdbed}input,select{width:100%;border:1px solid #35435d;border-radius:10px;background:#0c1425;color:var(--text);font:inherit;padding:12px 14px;outline:none}input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px #8dc5ff22}input::placeholder{color:#697991}
.row{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:14px}.row input{width:17px;height:17px;accent-color:#8dc5ff}.submit{margin-top:22px;border:0;border-radius:10px;background:#a8d3ff;color:#0b1628;font:700 15px system-ui;padding:13px 18px;cursor:pointer}.submit:hover{background:#c4e1ff}.hint{color:var(--muted);font-size:13px;margin:9px 0 0}
.result{margin-top:28px}.result-head{display:flex;justify-content:space-between;gap:16px;align-items:start}.badge{color:var(--green);border:1px solid #316a5d;background:#14352e;padding:5px 10px;border-radius:999px;font-size:12px;white-space:nowrap}.subtle{color:var(--muted)}.source{border-top:1px solid var(--line);padding:18px 0}.source h3{margin:0 0 5px;font-size:17px;line-height:1.4}.source a{color:var(--accent);text-decoration:none}.source a:hover{text-decoration:underline}.abstract{color:#c1ccdd;font-size:14px;margin:9px 0 0}.notice{border-left:3px solid #e2b66e;padding:10px 14px;background:#2b251b;color:#ead5b0;border-radius:4px;font-size:14px;margin:18px 0}.error{border-color:#794251;color:#ffd1d8;background:#351d28}.report{display:inline-block;color:var(--accent);margin:8px 0 14px}.empty{color:var(--muted);padding:15px 0}@media(max-width:650px){.wrap{padding-top:34px}.grid{grid-template-columns:1fr}.wide{grid-column:auto}.panel{padding:19px}.result-head{display:block}.badge{display:inline-block;margin-top:8px}}
"""


def _page(body: str = "") -> bytes:
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0b1020"><title>ResearchOS — Research workspace</title><style>{STYLE}</style></head>
<body><main class="wrap"><div class="brand">ResearchOS · Research workspace</div>
<h1>Start with a question.</h1><p class="intro">Build a traceable research brief from a scientific question. Search scholarly literature, keep source records, and see what the workflow has—and hasn’t—done.</p>
<section class="panel"><form action="/research" method="post" onsubmit="const b=this.querySelector('button');b.disabled=true;b.textContent='Working…';">
<div class="grid"><div class="field wide"><label for="topic">Research question or topic</label><input id="topic" name="topic" required maxlength="1000" placeholder="e.g. Does federated learning improve zero-day attack detection?" value=""></div>
<div class="field"><label for="field">Research field</label><input id="field" name="field" required maxlength="200" placeholder="e.g. Computer Science" value=""></div>
<div class="field"><label for="format">Brief format</label><select id="format" name="format"><option value="markdown">Markdown (available)</option></select></div>
<div class="field"><label for="max-papers">Maximum candidate papers</label><select id="max-papers" name="max_papers"><option>5</option><option selected>10</option><option>20</option><option>50</option></select></div>
<div class="field" style="justify-content:end"><label class="row"><input type="checkbox" name="search" value="yes" checked> Search scholarly providers for candidate literature</label><p class="hint">Uncheck to create a brief without an internet search.</p></div></div>
<button class="submit" type="submit">Create research brief <span aria-hidden="true">→</span></button></form></section>{body}
<p class="hint" style="margin-top:22px">ResearchOS v0.2 · Literature search results are leads, not verified evidence. This release does not run experiments or generate findings.</p>
</main></body></html>"""
    return document.encode("utf-8")


def _render_result(request_data: dict, payload: dict) -> str:
    works = payload.get("works", [])
    items = []
    for work in works:
        title = escape(work.get("title") or "Untitled work")
        url = escape(work.get("url") or "", quote=True)
        authors = escape(", ".join(work.get("authors", [])) or "Author information unavailable")
        year = escape(str(work.get("year") or "Year unavailable"))
        venue = escape(work.get("venue") or "Venue unavailable")
        doi = work.get("doi")
        doi_link = f' · <a href="{escape(doi, quote=True)}" target="_blank" rel="noopener">DOI</a>' if doi else ""
        abstract = f'<p class="abstract">{escape(work["abstract"])}</p>' if work.get("abstract") else ""
        items.append(f'<article class="source"><h3><a href="{url}" target="_blank" rel="noopener">{title}</a></h3><div class="subtle">{authors} · {year} · {venue}{doi_link}</div>{abstract}</article>')
    discovery_status = payload.get("workflow", [{}, {}])[1].get("status")
    completeness = payload.get("search_completeness")
    search_status = ("Sources found" if works else
                     "Search failed" if discovery_status == "failed" else
                     "Partial search" if discovery_status == "partial" else
                     "No results" if discovery_status == "completed_no_results" else "Search skipped")
    report_url = f"/runs/{escape(payload['run_id'], quote=True)}/brief"
    source_block = "".join(items) if items else '<p class="empty">No candidate papers are listed for this run.</p>'
    if completeness in {"failed", "partial"}:
        failures = [f"{escape(item.get('provider', 'Provider'))}: {escape(item.get('error') or item.get('status', 'failed'))}"
                    for item in payload.get("provider_outcomes", []) if item.get("status") == "failed"]
        if failures:
            source_block += '<div class="notice">Provider issues: ' + "; ".join(failures) + "</div>"
    return f'''<section class="panel result"><div class="result-head"><div><div class="brand">Research brief</div><h2>{escape(request_data["topic"])}</h2><div class="subtle">{escape(request_data["research_field"])} · Run <code>{escape(payload["run_id"])}</code></div></div><span class="badge">{search_status} · {len(works)}</span></div>
<div class="notice">Discovery results are unreviewed metadata, not evidence synthesis. No experiment was run and no conclusion was generated.</div>
<a class="report" href="{report_url}">Open Markdown brief</a><h2>Candidate literature</h2>{source_block}</section>'''


def make_handler(output_dir: str | Path = "research_runs", default_max_papers: int = 10):
    agent_workflows = WorkflowStore(output_dir, default_agent_registry())
    projects = ProjectStore(output_dir)
    plugins = PluginRegistry(output_dir)
    class ResearchOSHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, content: bytes, content_type: str = "text/html; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, status: int, value: dict | list) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _send_download(self, content: bytes, content_type: str, filename: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def _read_json(self, maximum: int = 30_000_000) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > maximum:
                raise ValueError("Request body is invalid or too large.")
            body = self.rfile.read(length)
            value = json.loads(body.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("JSON request must be an object.")
            return value

        def _ecosystem(self, method: str) -> bool:
            """Authenticated v0.8 API routes; return False for other paths."""
            parsed = urlsplit(self.path)
            path = parsed.path
            if not (path == "/api/projects" or path.startswith("/api/projects/") or
                    path == "/api/plugins" or path.startswith("/api/plugins/")):
                return False
            auth = self.headers.get("Authorization", "")
            scheme, _, token = auth.partition(" ")
            actor = authenticate(output_dir, token) if scheme.lower() == "bearer" else None
            if actor is None:
                self._send_json(401, {"error": "A valid bearer token is required."})
                return True
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                if path == "/api/plugins" and method == "GET":
                    self._send_json(200, {"plugins": plugins.list()}); return True
                if path == "/api/plugins" and method == "POST":
                    if not actor.get("admin"):
                        raise PermissionError("Administrator role is required to install plugins.")
                    self._send_json(201, {"plugin": plugins.install(self._read_json(500_000), actor["username"])}); return True
                plugin_enable = re.fullmatch(r"/api/plugins/([a-z][a-z0-9_.-]{1,63})/enabled", path)
                plugin_action = re.fullmatch(r"/api/plugins/([a-z][a-z0-9_.-]{1,63})/actions/([a-z][a-z0-9_.-]{0,63})", path)
                if plugin_enable and method == "PUT":
                    if not actor.get("admin"):
                        raise PermissionError("Administrator role is required to enable plugins.")
                    data = self._read_json(10_000)
                    self._send_json(200, {"plugin": plugins.set_enabled(plugin_enable.group(1), data.get("enabled"), actor["username"])}); return True
                if plugin_action and method == "POST":
                    self._send_json(200, {"result": plugins.run(plugin_action.group(1), plugin_action.group(2), self._read_json(100_000))}); return True
                if path == "/api/projects" and method == "GET":
                    limit = int(query.get("limit", ["50"])[0])
                    cursor = query.get("cursor", [None])[0]
                    self._send_json(200, page_items(projects.list(actor), limit, cursor)); return True
                if path == "/api/projects" and method == "POST":
                    self._send_json(201, {"project": projects.create(self._read_json(16_000), actor)}); return True
                project_path = re.fullmatch(r"/api/projects/([0-9a-f-]{36})(?:/(.*))?", path)
                if project_path:
                    project_id, suffix = project_path.groups()
                    if not suffix and method == "GET":
                        self._send_json(200, {"project": projects.get(project_id, actor)}); return True
                    if suffix == "lifecycle" and method == "GET":
                        project = projects.get(project_id, actor, "viewer")
                        self._send_json(200, {"lifecycle": project_lifecycle(output_dir, project)}); return True
                    if not suffix and method == "PUT":
                        self._send_json(200, {"project": projects.update(project_id, self._read_json(16_000), actor)}); return True
                    if suffix == "members" and method == "POST":
                        self._send_json(200, {"project": projects.add_member(project_id, self._read_json(16_000), actor)}); return True
                    member_path = re.fullmatch(r"members/([a-z][a-z0-9_.-]{1,63})", suffix or "")
                    if member_path and method == "DELETE":
                        self._send_json(200, {"project": projects.remove_member(project_id, member_path.group(1), actor)}); return True
                    if suffix == "collections" and method == "POST":
                        self._send_json(201, {"project": projects.create_collection(project_id, self._read_json(100_000), actor)}); return True
                    collection_path = re.fullmatch(r"collections/([0-9a-f-]{36})", suffix or "")
                    if collection_path and method == "PUT":
                        self._send_json(200, {"project": projects.update_collection(project_id, collection_path.group(1), self._read_json(100_000), actor)}); return True
                    if suffix == "reviews" and method == "POST":
                        self._send_json(201, {"project": projects.add_review(project_id, self._read_json(16_000), actor)}); return True
                    if suffix == "revisions" and method == "GET":
                        limit = int(query.get("limit", ["50"])[0]); cursor = query.get("cursor", [None])[0]
                        self._send_json(200, page_items(projects.revisions(project_id, actor), limit, cursor)); return True
                self._send_json(405, {"error": "Method or API route is not supported."})
            except PermissionError as exc:
                self._send_json(403, {"error": str(exc)})
            except FileNotFoundError as exc:
                self._send_json(404, {"error": str(exc)})
            except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._send_json(400, {"error": str(exc) or "Invalid request."})
            except (OSError, RuntimeError) as exc:
                self._send_json(500, {"error": str(exc)})
            return True

        def do_GET(self) -> None:
            if self._ecosystem("GET"):
                return
            if self.path == "/api/health":
                root = Path(output_dir)
                progress_ok = False
                try:
                    progress = json.loads((PROJECT_ROOT / "BUILD_PROGRESS.json").read_text(encoding="utf-8"))
                    progress_ok = isinstance(progress, dict) and isinstance(progress.get("release"), str) and isinstance(progress.get("next"), list)
                except (OSError, ValueError, UnicodeDecodeError):
                    pass
                storage_location = root if root.exists() else root.parent
                checks = {"storage_location_writable": storage_location.is_dir() and os.access(storage_location, os.W_OK),
                          "release_progress_readable": progress_ok}
                healthy = all(checks.values())
                self._send_json(200 if healthy else 503, {"status": "ok" if healthy else "degraded", "checks": checks})
                return
            if self.path == "/api/quality":
                try:
                    self._send_json(200, scientific_quality_report(output_dir))
                except (OSError, ValueError, RuntimeError) as exc:
                    self._send_json(500, {"error": str(exc)})
                return
            if self.path == "/":
                try:
                    self._send(200, (STATIC_DIR / "index.html").read_bytes())
                except OSError:
                    self._send(500, b"ResearchOS workspace assets are missing.", "text/plain; charset=utf-8")
                return
            asset = re.fullmatch(r"/assets/(app\.css|app-extra\.css|app\.js)", self.path)
            if asset:
                content_type = "text/css; charset=utf-8" if asset.group(1).endswith("css") else "text/javascript; charset=utf-8"
                try:
                    self._send(200, (STATIC_DIR / asset.group(1)).read_bytes(), content_type)
                except OSError:
                    self._send(404, b"Asset not found.", "text/plain; charset=utf-8")
                return
            if self.path == "/api/state":
                # Configuration is checked locally; API credentials are never included in status output.
                progress = get_build_progress()
                sandbox = _configured_sandbox_runner()
                state = {"versions": VERSION_STATUS, "current_release": progress.get("release", "v0.5"),
                         "ai_provider_configured": ClaimExtractionConfig.from_environment() is not None,
                         "sandbox_image_configured": sandbox is not None,
                         "sandbox_profile": ({"python_version": sandbox.profile.python_version,
                             "packages": sandbox.approved_packages,
                             "manifest_sha256": sandbox.profile.manifest_sha256} if sandbox else None),
                         "build_progress": progress}
                self._send_json(200, state)
                return
            if self.path == "/api/agent-workflows":
                self._send_json(200, {"workflows": agent_workflows.list()})
                return
            if self.path == "/api/writing/catalog":
                self._send_json(200, writing_catalog(output_dir))
                return
            if self.path == "/api/writing/drafts":
                self._send_json(200, {"drafts": list_drafts(output_dir)})
                return
            writing_export_match = re.fullmatch(r"/api/writing/drafts/([0-9a-f-]{36})/exports/([A-Za-z0-9][A-Za-z0-9._-]{0,99})", self.path)
            if writing_export_match:
                try:
                    path, item = get_export_path(output_dir, writing_export_match.group(1), writing_export_match.group(2))
                    self._send_download(path.read_bytes(), item["content_type"], item["filename"])
                except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
                    self._send_json(404, {"error": str(exc)})
                return
            writing_draft_match = re.fullmatch(r"/api/writing/drafts/([0-9a-f-]{36})", self.path)
            if writing_draft_match:
                try:
                    self._send_json(200, {"draft": get_draft(output_dir, writing_draft_match.group(1))})
                except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
                    self._send_json(404, {"error": str(exc)})
                return
            workflow_match = re.fullmatch(r"/api/agent-workflows/([0-9a-f-]{36})", self.path)
            if workflow_match:
                try:
                    self._send_json(200, {"workflow": agent_workflows.get(workflow_match.group(1))})
                except (ValueError, FileNotFoundError) as exc:
                    self._send_json(404, {"error": str(exc)})
                return
            version_match = re.fullmatch(r"/api/versions/(v[1-9])", self.path)
            if version_match:
                try:
                    self._send_json(200, {"tracking": version_match.group(1), "content": get_version_note(version_match.group(1))})
                except (ValueError, FileNotFoundError):
                    self._send_json(404, {"error": "Version note not found."})
                return
            if self.path == "/api/chats":
                self._send_json(200, {"chats": list_chats(output_dir)})
                return
            if self.path == "/api/documents":
                self._send_json(200, {"documents": list_documents(output_dir)})
                return
            if self.path == "/api/datasets":
                self._send_json(200, {"datasets": list_datasets(output_dir)})
                return
            dataset_file_match = re.fullmatch(r"/api/datasets/([0-9a-f]{64})/file", self.path)
            if dataset_file_match:
                try:
                    self._send(200, dataset_path(output_dir, dataset_file_match.group(1)).read_bytes(), "application/octet-stream")
                except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
                    self._send_json(404, {"error": str(exc)})
                return
            if self.path == "/api/experiments":
                self._send_json(200, {"experiments": list_experiments(output_dir)})
                return
            if self.path == "/api/evaluations":
                self._send_json(200, {"evaluations": list_evaluations(output_dir)})
                return
            experiment_match = re.fullmatch(r"/api/experiments/([0-9a-f-]{36})", self.path)
            if experiment_match:
                try:
                    self._send_json(200, {"experiment": get_experiment(output_dir, experiment_match.group(1))})
                except (ValueError, FileNotFoundError, OSError) as exc:
                    self._send_json(404, {"error": str(exc)})
                return
            artifact_match = re.fullmatch(r"/api/experiments/([0-9a-f-]{36})/artifacts/([A-Za-z0-9][A-Za-z0-9._-]{0,99})", self.path)
            if artifact_match:
                try:
                    path = experiment_artifact_path(output_dir, artifact_match.group(1), artifact_match.group(2))
                    self._send(200, path.read_bytes(), "application/octet-stream")
                except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
                    self._send_json(404, {"error": str(exc)})
                return
            if self.path == "/api/methodology/designs":
                self._send_json(200, {"designs": list_study_designs(output_dir)})
                return
            methodology_design_match = re.fullmatch(r"/api/methodology/designs/([0-9a-f-]{36})", self.path)
            if methodology_design_match:
                try:
                    self._send_json(200, {"design": get_study_design(output_dir, methodology_design_match.group(1))})
                except (ValueError, FileNotFoundError, OSError) as exc:
                    self._send_json(404, {"error": str(exc)})
                return
            if self.path == "/api/claims":
                graph = load_graph(output_dir)
                self._send_json(200, graph)
                return
            if self.path.startswith("/api/search?"):
                from urllib.parse import parse_qs, urlsplit
                query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
                try:
                    self._send_json(200, {"matches": search_knowledge(output_dir, query)})
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            document_match = re.fullmatch(r"/api/documents/([a-f0-9]{64})", self.path)
            if document_match:
                document = next((item for item in list_documents(output_dir) if item["document_id"] == document_match.group(1)), None)
                self._send_json(200 if document else 404, document or {"error": "Document not found."})
                return
            pdf_match = re.fullmatch(r"/api/documents/([a-f0-9]{64})/file", self.path)
            if pdf_match:
                file_path = Path(output_dir) / "documents" / f"{pdf_match.group(1)}.pdf"
                try:
                    self._send(200, file_path.read_bytes(), "application/pdf")
                except OSError:
                    self._send(404, b"Source PDF not found.", "text/plain; charset=utf-8")
                return
            chat_match = re.fullmatch(r"/api/chats/([0-9a-f-]{36})", self.path)
            if chat_match:
                try:
                    self._send_json(200, get_chat(output_dir, chat_match.group(1)))
                except (ValueError, FileNotFoundError, OSError):
                    self._send_json(404, {"error": "Chat not found."})
                return
            match = re.fullmatch(r"/runs/([0-9]{8}T[0-9]{6}Z-[a-z0-9-]+-[a-f0-9]{8})/brief", self.path)
            if match:
                report_file = Path(output_dir) / match.group(1) / "research_brief.md"
                try:
                    report = report_file.read_bytes()
                except OSError:
                    self._send(404, b"Research brief not found.", "text/plain; charset=utf-8")
                    return
                self._send(200, report, "text/markdown; charset=utf-8")
                return
            self._send(404, _page('<section class="panel"><h2>Page not found</h2><a href="/">Return to research workspace</a></section>'))

        def do_POST(self) -> None:
            if self._ecosystem("POST"):
                return
            if self.path == "/api/writing/drafts":
                try:
                    draft = create_draft(output_dir, self._read_json(1_000_000))
                    self._send_json(201, {"draft": draft})
                except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            writing_review_match = re.fullmatch(r"/api/writing/drafts/([0-9a-f-]{36})/review", self.path)
            if writing_review_match:
                try:
                    data = self._read_json(20_000)
                    record = review_draft(output_dir, writing_review_match.group(1), data.get("action", ""), data.get("note", ""))
                    self._send_json(200, {"draft": record})
                except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            writing_export_match = re.fullmatch(r"/api/writing/drafts/([0-9a-f-]{36})/export", self.path)
            if writing_export_match:
                try:
                    data = self._read_json(20_000)
                    item = export_draft(output_dir, writing_export_match.group(1), data.get("format", ""))
                    self._send_json(201, {"export": item})
                except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/agent-workflows":
                try:
                    data = self._read_json(1_000_000)
                    self._send_json(201, {"workflow": agent_workflows.create(data)})
                except (ValueError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            review_match = re.fullmatch(r"/api/agent-workflows/([0-9a-f-]{36})/tasks/([A-Za-z0-9_-]{1,64})/review", self.path)
            if review_match:
                try:
                    data = self._read_json(1_000_000)
                    workflow = agent_workflows.review(review_match.group(1), review_match.group(2),
                                                      data.get("action", ""), data.get("output"))
                    self._send_json(200, {"workflow": workflow})
                except (ValueError, FileNotFoundError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            resume_match = re.fullmatch(r"/api/agent-workflows/([0-9a-f-]{36})/resume", self.path)
            if resume_match:
                try:
                    self._send_json(200, {"workflow": agent_workflows.run(resume_match.group(1))})
                except (ValueError, FileNotFoundError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/datasets":
                try:
                    data = self._read_json(70_000_000)
                    encoded = data.get("data_base64", "")
                    if not isinstance(encoded, str) or len(encoded) > 70_000_000:
                        raise ValueError("Dataset upload encoding is invalid or too large.")
                    content = base64.b64decode(encoded, validate=True)
                    dataset = import_dataset(output_dir, data.get("filename", ""), content)
                    self._send_json(201, {"dataset": dataset})
                except (ValueError, OSError, base64.binascii.Error) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/experiments":
                try:
                    data = self._read_json(12_000_000)
                    runner = _configured_sandbox_runner()
                    record = run_experiment(output_dir, data, runner)
                    self._send_json(201, {"experiment": record})
                except (ValueError, FileNotFoundError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            evaluation_match = re.fullmatch(r"/api/datasets/([0-9a-f]{64})/evaluate", self.path)
            if evaluation_match:
                try:
                    data = self._read_json(20_000)
                    record = evaluate_prediction_columns(output_dir, evaluation_match.group(1), data.get("task", ""),
                        data.get("actual_column", ""), data.get("predicted_column", ""))
                    self._send_json(201, {"evaluation": record})
                except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/chats":
                try:
                    self._send_json(201, create_chat(output_dir))
                except OSError as exc:
                    self._send_json(500, {"error": str(exc)})
                return
            if self.path == "/api/chat":
                try:
                    data = self._read_json(100_000)
                    user = append_chat_message(output_dir, data.get("chat_id", ""), "user", data.get("message", ""),
                                               data.get("output_style", "balanced"), data.get("input_style", "chat"))
                    chat_config = ClaimExtractionConfig.from_environment()
                    if chat_config is not None:
                        chat_messages = get_chat(output_dir, data["chat_id"])["messages"]
                        assistant_text = OpenAICompatibleChatProvider(chat_config).respond(
                            chat_messages, data.get("output_style", "balanced"))
                        response_source = "ai"
                    else:
                        assistant_text = workspace_reply(user["content"], data.get("output_style", "balanced"))
                        response_source = "local"
                    assistant = append_chat_message(output_dir, data["chat_id"], "assistant", assistant_text,
                                                    data.get("output_style", "balanced"), data.get("input_style", "chat"),
                                                    response_source)
                    self._send_json(200, {"user_message": user, "assistant_message": assistant,
                                          "chat": get_chat(output_dir, data["chat_id"])})
                except (ValueError, KeyError, FileNotFoundError, OSError, RuntimeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/upload":
                try:
                    data = self._read_json()
                    document = store_pdf(output_dir, data.get("filename", ""), data.get("data", ""))
                    self._send_json(201, {"document": document})
                except (ValueError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/claims":
                try:
                    data = self._read_json(50_000)
                    claim = add_claim(output_dir, data.get("document_id", ""), int(data.get("page_number", 0)),
                                      data.get("passage_text", ""), data.get("claim_text", ""),
                                      data.get("section_name"), data.get("evidence_category", "unverified_claim"),
                                      data.get("extraction_method", "researcher_entered"))
                    self._send_json(201, {"claim": claim})
                except (ValueError, OSError, TypeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/claims/extract":
                try:
                    data = self._read_json(30_000)
                    config = ClaimExtractionConfig.from_environment()
                    if config is None:
                        raise ValueError("Configure RESEARCHOS_AI_PROVIDER=openai_compatible, RESEARCHOS_AI_MODEL, and RESEARCHOS_API_KEY to enable claim suggestions.")
                    document = next((item for item in list_documents(output_dir) if item["document_id"] == data.get("document_id")), None)
                    if document is None:
                        raise ValueError("Source document is not in the local library.")
                    page_number = int(data.get("page_number", 0))
                    page = next((item for item in document.get("pages", []) if item["page"] == page_number), None)
                    if page is None or not page.get("text", "").strip():
                        raise ValueError("Selected page has no extracted text. Run OCR or choose another page.")
                    candidates = OpenAICompatibleClaimExtractor(config).extract(page["text"])
                    for candidate in candidates:
                        candidate.update({"document_id": document["document_id"], "page_number": page_number,
                                          "section_name": page.get("section_name"), "passage_text": page["text"]})
                    self._send_json(200, {"candidates": candidates, "count": len(candidates),
                                          "notice": "AI suggestions are provisional and require researcher review."})
                except (ValueError, RuntimeError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/relationships":
                try:
                    data = self._read_json(20_000)
                    edge = add_relationship(output_dir, data.get("source_id", ""), data.get("target_id", ""),
                                            data.get("relationship", ""), data.get("notes", ""))
                    self._send_json(201, {"edge": edge})
                except (ValueError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/relationships/analyze":
                try:
                    data = self._read_json(10_000)
                    config = ClaimExtractionConfig.from_environment()
                    if config is None:
                        raise ValueError("Configure an OpenAI-compatible AI provider, model, and API key to enable relation suggestions.")
                    graph = load_graph(output_dir)
                    claims = {claim["claim_id"]: claim for claim in graph.get("claims", [])}
                    left = claims.get(data.get("left_claim_id"))
                    right = claims.get(data.get("right_claim_id"))
                    if left is None or right is None:
                        raise ValueError("Both selected claims must exist in the local evidence graph.")
                    result = OpenAICompatibleRelationshipAnalyzer(config).analyze(left, right)
                    result.update({"left_claim_id": left["claim_id"], "right_claim_id": right["claim_id"]})
                    self._send_json(200, result)
                except (ValueError, RuntimeError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/methodology/analyze":
                try:
                    data = self._read_json(50_000)
                    result = analyze_research_problem(data.get("topic", ""), data.get("research_field", ""),
                        data.get("research_type"), data.get("problem_statement"), data.get("objectives"), data.get("research_questions"))
                    payload = result.to_dict()
                    selected_ids = data.get("evidence_claim_ids", [])
                    if not isinstance(selected_ids, list) or len(selected_ids) > 30 or any(not isinstance(item, str) for item in selected_ids):
                        raise ValueError("Select up to 30 local evidence claims.")
                    graph = load_graph(output_dir)
                    claims = {claim["claim_id"]: claim for claim in graph.get("claims", [])}
                    if any(claim_id not in claims for claim_id in selected_ids):
                        raise ValueError("A selected literature claim is not in the local evidence graph.")
                    basis = []
                    for claim_id in selected_ids:
                        claim = claims[claim_id]
                        source = claim["source"]
                        basis.append({"claim_id": claim_id, "claim": claim["original_text"],
                            "verification_status": claim["verification_status"], "evidence_category": claim["evidence_category"],
                            "page_number": source["page_number"], "section_name": source.get("section_name"),
                            "source_excerpt": source["text"][:500]})
                    payload["literature_context"] = basis
                    if not basis or any(item["verification_status"] not in {"reviewed", "verified"} for item in basis):
                        payload["missing_information"].append("Select literature claims reviewed against their source passages before treating questions as literature-grounded.")
                    self._send_json(200, payload)
                except (ValueError, TypeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/methodology/statistics":
                try:
                    data = self._read_json(20_000)
                    self._send_json(200, recommend_statistical_methods(data.get("outcome_type", "unknown"),
                        data.get("design", "unknown"), data.get("sample_size"), data.get("independent_observations"),
                        bool(data.get("distribution_assessed", False)), bool(data.get("variance_assessed", False)),
                        data.get("dataset_summary")))
                except (ValueError, TypeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/methodology/refine":
                try:
                    data = self._read_json(50_000)
                    config = ClaimExtractionConfig.from_environment()
                    if config is None:
                        raise ValueError("Configure an OpenAI-compatible provider, model, and API key to enable question refinement.")
                    selected_ids = data.get("evidence_claim_ids", [])
                    if not isinstance(selected_ids, list) or len(selected_ids) > 30 or any(not isinstance(item, str) for item in selected_ids):
                        raise ValueError("Select up to 30 reviewed literature claims.")
                    graph = load_graph(output_dir)
                    claims = {claim["claim_id"]: claim for claim in graph.get("claims", [])}
                    if any(claim_id not in claims for claim_id in selected_ids):
                        raise ValueError("A selected literature claim is not in the local evidence graph.")
                    selected_claims = [claims[claim_id] for claim_id in selected_ids]
                    result = OpenAICompatibleQuestionRefiner(config).refine(data.get("problem_statement", ""),
                        data.get("research_type", ""), data.get("objectives", []), selected_claims)
                    result["evidence_context"] = [{"claim_id": claim["claim_id"], "claim": claim["original_text"],
                        "page_number": claim["source"]["page_number"], "section_name": claim["source"].get("section_name"),
                        "document_id": claim["source"]["document_id"],
                        "verification_status": claim["verification_status"]} for claim in selected_claims]
                    self._send_json(200, result)
                except (ValueError, RuntimeError, OSError, TypeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/methodology/designs":
                try:
                    data = self._read_json(100_000)
                    self._send_json(201, {"design": save_study_design(output_dir, data)})
                except (ValueError, TypeError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path.startswith("/api/methodology/"):
                self._send_json(404, {"error": "Methodology route not found."})
                return
            if self.path == "/api/research":
                try:
                    data = self._read_json(100_000)
                    request = create_research_request(data.get("topic", ""), data.get("field", ""), "markdown")
                    max_papers = int(data.get("max_papers", 10))
                    result = run_research(request, output_dir=output_dir, max_papers=max_papers,
                                          search=bool(data.get("search", True)))
                    run_data = json.loads((result.directory / "run.json").read_text(encoding="utf-8"))
                    response_data = {"run_id": result.run_id, "source_count": result.source_count,
                                          "report_url": f"/runs/{result.run_id}/brief",
                                          "search_completeness": run_data.get("search_completeness"),
                                          "provider_outcomes": run_data.get("provider_outcomes", [])}
                    chat_id = data.get("chat_id")
                    if chat_id:
                        append_chat_message(output_dir, chat_id, "user", f"Create a research brief about {request.topic} ({request.research_field}).")
                        summary = (f"Research run saved: {result.run_id}\nCandidate papers: {result.source_count}\n"
                                   f"Search status: {run_data.get('search_completeness')}\n\n"
                                   f"Markdown brief: {response_data['report_url']}")
                        append_chat_message(output_dir, chat_id, "assistant", summary)
                        response_data["chat"] = get_chat(output_dir, chat_id)
                    self._send_json(201, response_data)
                except (ValueError, RuntimeError, OSError, TypeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if self.path != "/research":
                self._send(404, _page('<section class="panel"><h2>Page not found</h2><a href="/">Return to research workspace</a></section>'))
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 65536:
                    raise ValueError("Form submission is too large.")
                form = parse_qs(self.rfile.read(length).decode("utf-8"), max_num_fields=20)
                topic = form.get("topic", [""])[0]
                research_field = form.get("field", [""])[0]
                output_format = form.get("format", ["markdown"])[0]
                request = create_research_request(topic, research_field, output_format)
                max_papers = int(form.get("max_papers", [str(default_max_papers)])[0])
                result = run_research(request, output_dir=output_dir, max_papers=max_papers, search="search" in form)
                payload = json.loads((result.directory / "run.json").read_text(encoding="utf-8"))
                rendered = _render_result(request.to_dict(), payload)
                self._send(200, _page(rendered))
            except (ValueError, RuntimeError, OSError, UnicodeDecodeError) as exc:
                error = f'<section class="panel result"><div class="notice error">{escape(str(exc))}</div><a href="/">Back to the research workspace</a></section>'
                self._send(400, _page(error))

        def do_PUT(self) -> None:
            if self._ecosystem("PUT"):
                return
            writing_edit_match = re.fullmatch(r"/api/writing/drafts/([0-9a-f-]{36})", self.path)
            if writing_edit_match:
                try:
                    data = self._read_json(2_100_000)
                    draft = edit_draft(output_dir, writing_edit_match.group(1), data.get("content"))
                    self._send_json(200, {"draft": draft})
                except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            methodology_approve_match = re.fullmatch(r"/api/methodology/designs/([0-9a-f-]{36})/approve", self.path)
            methodology_design_match = re.fullmatch(r"/api/methodology/designs/([0-9a-f-]{36})", self.path)
            if methodology_approve_match:
                try:
                    data = self._read_json(10_000)
                    design = approve_study_design(output_dir, methodology_approve_match.group(1), data.get("reviewer_note", ""))
                    self._send_json(200, {"design": design})
                except (ValueError, FileNotFoundError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if methodology_design_match:
                try:
                    data = self._read_json(100_000)
                    design = save_study_design(output_dir, data, methodology_design_match.group(1))
                    self._send_json(200, {"design": design})
                except (ValueError, FileNotFoundError, OSError, TypeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            match = re.fullmatch(r"/api/chats/([0-9a-f-]{36})/messages/([0-9a-f-]{36})", self.path)
            review_match = re.fullmatch(r"/api/claims/([0-9a-f-]{36})/review", self.path)
            relationship_review_match = re.fullmatch(r"/api/relationships/([0-9a-f-]{36})/review", self.path)
            if review_match:
                try:
                    data = self._read_json(20_000)
                    claim = update_claim_review(output_dir, review_match.group(1), data.get("status", ""), data.get("reviewer_note", ""))
                    self._send_json(200, {"claim": claim})
                except (ValueError, FileNotFoundError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if relationship_review_match:
                try:
                    data = self._read_json(20_000)
                    edge = review_relationship(output_dir, relationship_review_match.group(1),
                                               data.get("status", ""), data.get("reviewer_note", ""))
                    self._send_json(200, {"edge": edge})
                except (ValueError, FileNotFoundError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                return
            if not match:
                self._send_json(404, {"error": "Message not found."})
                return
            try:
                data = self._read_json(100_000)
                original_chat = get_chat(output_dir, match.group(1))
                edited_index = next((i for i, item in enumerate(original_chat["messages"])
                                     if item["id"] == match.group(2) and item["role"] == "user"), None)
                if edited_index is None:
                    raise FileNotFoundError("User message not found.")
                chat_config = ClaimExtractionConfig.from_environment()
                if chat_config is not None:
                    context = original_chat["messages"][:edited_index] + [{"role": "user", "content": data.get("message", "")}]
                    assistant_text = OpenAICompatibleChatProvider(chat_config).respond(
                        context, data.get("output_style", "balanced"))
                    response_source = "ai"
                else:
                    assistant_text = workspace_reply(data.get("message", ""), data.get("output_style", "balanced"))
                    response_source = "local"
                result = edit_chat_message(output_dir, match.group(1), match.group(2), data.get("message", ""),
                                           data.get("output_style", "balanced"), data.get("input_style", "chat"),
                                           assistant_text, response_source)
                self._send_json(200, result)
            except (ValueError, FileNotFoundError, OSError, RuntimeError) as exc:
                self._send_json(400, {"error": str(exc)})

        def do_DELETE(self) -> None:
            if self._ecosystem("DELETE"):
                return
            self._send_json(404, {"error": "Route not found."})

        def log_message(self, format: str, *args) -> None:
            try:
                message = format % args
            except (TypeError, ValueError):
                message = format
            print(f"ResearchOS web: {self.address_string()} {message}")

    return ResearchOSHandler


def serve(host: str = "127.0.0.1", port: int = 8000, output_dir: str | Path = "research_runs") -> None:
    handler = make_handler(output_dir)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"ResearchOS is running at http://{host}:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nResearchOS stopped.")
    finally:
        server.server_close()

# ResearchOS

ResearchOS is an open-source foundation for evidence-first, AI-assisted scientific research. Its long-term direction is to coordinate literature discovery, study design, experiments, statistical review, verification, and scientific writing across domain-specific methods.

## What works

- Validate and store a structured research request.
- Create a transparent research plan with explicit workflow stages.
- Search OpenAlex and Crossref for scholarly works (optional network access).
- Aggregate provider outcomes, deduplicate records by normalized DOI, and preserve each provider's identifier, source URL, and retrieval timestamp.
- Save a run record and generate a Markdown research brief with linked sources.
- Keep findings, hypotheses, and proposed experiments distinct. ResearchOS does not run experiments or generate research claims.
- Open a responsive, local-first browser workspace with persistent chats, edit-and-resend, voice input where supported, output/input style settings, PDF import, engine navigation, and a nine-release progress view.
- Analyze a supplied research problem for missing inputs, receive conditional domain/method and research-type-specific feasibility/sampling guidance, review candidate statistical methods for common continuous, binary, categorical, count, ordinal, and time-to-event cases, and save/approve a structured study design linked to reviewed evidence claims. Statistical suggestions remain conditional and do not calculate results. When configured, researchers can request editable question and hypothesis suggestions grounded only in selected, reviewed literature claims.
- Run schema-validated research-plan agents in durable task workflows. Tasks preserve provenance, provider/model and usage records, use explicit tool permissions, support restart/retry, and pause for researcher approval, edits, rejection, or revision. A deterministic local planner works without credentials; an optional OpenAI-compatible planner requires explicit configuration and confirmation.
- Create editable article, review, systematic-review, proposal, report, conference-paper, and theoretical-paper drafts from selected reviewed claims, approved study designs, successful experiments, and descriptive evaluations. Citations resolve to stored PDF hashes/pages or are marked for review; missing sections remain explicit. Researchers approve a specific draft revision before Markdown, LaTeX, DOCX, or PDF export.
- Import and validate CSV/JSON row datasets (up to 50 MiB, 1,000,000 rows, and 10,000,000 cells) with content hashes, heuristic column profiles, and lineage records. Compute descriptive classification/regression metrics and view confusion-matrix or actual-versus-predicted charts from supplied predictions. Python scripts and Python Jupyter notebooks are recorded locally and can run only in a digest-pinned Docker image with network disabled, read-only inputs, and resource limits; a running Docker daemon is required. Notebook markdown and saved outputs are preserved as input provenance, never executed or treated as results.

ResearchOS remains a local-first foundation, not an autonomous laboratory. Provider abstracts may be unavailable, and search results are discovery leads rather than proof that a claim is true. Providers are queried independently; an unavailable provider is recorded as a partial or failed search rather than silently treated as a successful empty search. Browser chat uses local workspace guidance when no AI provider is configured; with a provider configured, sending a message sends recent chat history to that provider and generated replies are labeled. AI replies are not verified research findings. In v0.3, researchers can optionally request claim and selected-pair relationship suggestions through an OpenAI-compatible endpoint; suggestions remain provisional. In v0.4, question and hypothesis suggestions can be requested from selected reviewed claims; each suggestion must cite those claims and remains proposed and editable. Methodology recommendations are conditional guidance and require researcher review; the app does not assert novelty, invent research gaps, or compute statistical results from unavailable data.

## Install

Python 3.10 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -e .
```

## First research run

```bash
researchos run \
  --topic "Federated learning for cybersecurity" \
  --field "Computer Science" \
  --format markdown \
  --output-dir research_runs \
  --max-papers 10
```

The run writes a JSON record and Markdown brief under a new run folder. Use `--no-search` to make a plan and report without contacting literature providers. Research run briefs are Markdown; the separate writing engine can export approved research drafts as Markdown, LaTeX, DOCX, or PDF.

## Browser interface

Start the local web interface with `researchos web`, then open [http://127.0.0.1:8000](http://127.0.0.1:8000). It runs on your computer and saves research runs, chats, imported PDFs, and datasets under `research_runs/`. Stop it with `Ctrl+C` in the terminal. Use **New research** to search OpenAlex and Crossref; turn off search to create a run without network access. PDF text extraction uses `pypdf`. Optional scanned-PDF OCR requires `pip install -e '.[ocr]'` and a `tesseract` executable on `PATH`. To enable AI chat and the explicit **Suggest claims from selected page (AI)**, **Compare selected claims**, and methodology refinement actions, set `RESEARCHOS_AI_PROVIDER=openai_compatible`, `RESEARCHOS_AI_MODEL`, and `RESEARCHOS_API_KEY`; optionally set `RESEARCHOS_AI_BASE_URL` for another OpenAI-compatible service. Sending a chat message transmits up to 16 recent messages to that endpoint; selected source text is sent only when the researcher requests a source suggestion. Provider charges/network access may apply; credentials stay server-side. To enable experiments, follow the [sandbox image recipe](sandbox-image/README.md), set `RESEARCHOS_SANDBOX_IMAGE` to the published digest-pinned reference and `RESEARCHOS_SANDBOX_MANIFEST` to a local JSON manifest, then start Docker. The manifest schema is `{ "schema_version": 1, "image": "local/research-python@sha256:<64 lowercase hex characters>", "python_version": "3.12.4", "packages": {"numpy": "2.1.3"} }`. Its package list is an operator attestation; ResearchOS validates the manifest and requested exact pins but does not inspect packages inside the image. Runtime installation and network access are disabled. Python `.ipynb` notebooks must use format 4 and plain Python cells; code cells run sequentially inside the sandbox. Markdown and saved outputs are retained only as provenance and never treated as results. Experiment code can write output files to `/artifacts`; up to 20 flat regular files and 16 MiB total are retained with SHA-256 hashes. CPU and memory are sampled while the container runs; very brief peaks may be missed. Runs require researcher confirmation; unavailable images or daemons fail closed, and submitted code is never run by host Python. Chat voice input depends on browser support.

To set up Gemini locally, copy `.env.example` to `.researchos.env`, enter your model ID and API key in that ignored local file, then start the web server with those variables loaded:

```bash
cp .env.example .researchos.env
# Edit .researchos.env and set RESEARCHOS_AI_MODEL and RESEARCHOS_API_KEY.
set -a
. ./.researchos.env
set +a
researchos web
```

Never commit `.researchos.env`; `.gitignore` excludes it.

ResearchOS is intended to bind to `127.0.0.1` by default. The built-in server does not provide TLS, and bearer authentication currently covers project/plugin routes rather than every legacy browser API. Do not bind it to a public or untrusted network. For a remote coding workspace, use a platform preview or a trusted reverse proxy that supplies transport security and access control for every route.

## Projects, API, and plugins

Create a local API user with `researchos auth create-user --output-dir research_runs --username alice --display-name "Alice Researcher"`. The command prints a high-entropy bearer token once; store it securely. The first account is an administrator. Revoke a user's tokens with `researchos auth revoke-user --output-dir research_runs --username alice`. Tokens are stored as SHA-256 hashes in a local file with owner-only permissions. The Projects view accepts a token for the current browser tab. All `/api/projects` and `/api/plugins` routes require bearer authentication. Project roles are `owner`, `editor`, `reviewer`, and `viewer`; collections contain typed references to existing ResearchOS records, and project revisions record the acting username. Project and revision listing use `limit` (1–100) and opaque `cursor` pagination.

The Python client is `researchos.client.ResearchOSClient`. It provides authenticated project creation, listing, editing, member assignment, collections, revisions, and plugin listing. Example:

```python
from researchos.client import ResearchOSClient

client = ResearchOSClient("http://127.0.0.1:8000", token="YOUR_LOCAL_TOKEN")
project = client.create_project("Sleep and learning")
for item in client.iter_projects(page_size=25):
    print(item["title"])
```

Use `client.project_lifecycle(project_id)` or open **Research lifecycle status** in a project card to see which collected records map to discovery, evidence review, study design, experiments, evaluation, writing, and project review. Missing and stale references are shown; a record is not treated as proof that a scientific stage is valid or complete.

Plugins use a JSON manifest with exact plugin API version, semantic version, dependency versions, permission names, and bounded JSON input/output contracts. Install and enable actions require an administrator token. The current host exposes two read-only literature-search adapters (`literature.crossref.search` and `literature.openalex.search`); these can make external requests only when explicitly invoked and only if both `external.network.read` and `literature.search` are declared. Manifests cannot load Python modules or execute arbitrary code. Unsupported handlers and permissions fail closed. Core workflows do not depend on plugins. See [v7.md](v7.md) for contract limitations and remaining ecosystem work.

## Reliability, backups, and verification

Use `researchos backup --output-dir research_runs --destination ../researchos-backup.tar.gz` to create a SHA-256-verified archive outside the live data directory. Check it with `researchos verify-backup ../researchos-backup.tar.gz`. Restore with `researchos restore ../researchos-backup.tar.gz --destination restored_research_runs`; restoration only writes into a new, non-existing directory. Backups include local users and bearer-token hashes, so protect the archive like the original workspace. `researchos quality --output-dir research_runs` and the browser Verification view check local JSON readability, dataset/PDF content hashes, and claim review state. These checks report integrity and process state; they do not score research quality or decide whether claims are true. `GET /api/health` reports basic storage-location and progress-file availability.

```bash
researchos request --topic "..." --field "..." --format markdown
```

## Research integrity

- Every discovered work retains provider identifiers, source URLs, and retrieval timestamps in `run.json`, including when DOI deduplication combines records.
- Search completeness and each provider outcome are recorded in `run.json`; all-provider failure is distinct from a successful search with zero results.
- Search metadata is labeled as discovery data, not validated evidence.
- The report does not invent hypotheses, results, citations, or statistical conclusions.
- Prediction metrics are computed only from supplied dataset rows and are descriptive; they do not validate model generalization or statistical significance. Experiment code runs only in the configured isolated Docker backend.
- AI claim and selected-pair relationship suggestions remain provisional and require researcher review; no batch contradiction analysis is provided. PDF table cells and reference DOI/year fields are heuristic candidates, not verified structures or normalized citations. Coordinate-aware table extraction, reference normalization, domain-specific review, and polished PDF/DOCX output remain in progress or planned; see [ROADMAP.md](ROADMAP.md) and the matching `vN.md` milestone report.

## Development

Run the test suite with `PYTHONPATH=src python -m unittest discover -s tests` (use `python3` if your system does not define `python`).

See [docs/architecture.md](docs/architecture.md) and [ROADMAP.md](ROADMAP.md) for the current module boundaries, nine-release plan, feedback, and remaining acceptance criteria.

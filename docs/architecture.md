# ResearchOS architecture

## Current architecture

ResearchOS is a local-first Python application with a CLI and browser workspace. The current workflow is:

```text
CLI / Browser workspace → validated research request → research orchestrator
                        → OpenAlex + Crossref → DOI deduplication / provider outcomes
                        → JSON run record + Markdown literature brief

Browser chat → local chat history → optional OpenAI-compatible provider
PDF upload → content hash + preserved original + page-aware extraction status
```

| Module | Current responsibility |
|---|---|
| `core` | Structured request validation and literature-run orchestration |
| `research.models` | Shared paper and provenance records |
| `research.providers` | OpenAlex and Crossref adapters with a shared search contract |
| `research.search` | Provider aggregation, DOI deduplication, completeness outcomes |
| `storage` | Portable research-run JSON records |
| `workspace` | Local chat records, release status, PDF storage and extraction metadata |
| `experiments` | Content-addressed CSV/JSON intake, transformation lineage, experiment records, and a fail-closed Docker runner |
| `web` | Local HTTP routes for chat, research runs, uploads, documents, and version status |
| `static` | Responsive browser interface with chat history, editing, voice input, uploads, output/input style controls, and engine navigation |

The browser chat persists messages locally and uses workspace guidance without a configured provider. When configured, it sends at most 16 recent messages to the selected OpenAI-compatible model and labels the generated response; provider failures are surfaced to the user. Generated text is not treated as verified research analysis. Voice input depends on browser Web Speech support. PDF text extraction uses `pypdf`; optional scanned-page OCR uses `pypdfium2`, Pillow, and the system `tesseract` command. OCR page/time limits and missing tools are reported explicitly.

## v0.3 Evidence Intelligence Engine (core acceptance complete)

The current v0.3 delivery adds the browser workspace and initial document-ingestion/evidence foundations. The full v0.3 target is claim- and evidence-level traceability:

- Imported documents retain source, SHA-256, import time, extraction method, page number, and extraction state.
- The current parser recognizes reference section lines with candidate DOI/year fields and groups obvious delimited table-like rows into cell candidates; these remain heuristic and page-located, not normalized citations or verified table structures.
- Claim records must remain provisional until researcher review and link to document/page/section passages.
- Evidence categories distinguish observed results, evidence types, interpretations, hypotheses, proposed experiments, and unverified claims.
- A lightweight graph should store typed nodes and relationships without a dedicated graph database.
- Current graph records support researcher-entered claims, page-linked passages, manual review, and proposed typed relationships. PDF metadata can create unverified paper/author nodes. Proposed relationships can be accepted or rejected with reviewer context, and the interface shows both claims beside their source passages. Optional OpenAI-compatible services can suggest claims from one selected page or compare a researcher-selected claim pair; each output stays provisional and requires explicit review. There is no project-wide contradiction scan.
- Contradiction detection should surface review candidates with methods and context; disagreement must not be labeled a contradiction automatically.
- PDF extraction should identify sections and references where supported, detect scanned documents, and report unsupported extraction honestly.

Follow-up limits are recorded in [v2.md](../v2.md): coordinate-aware table extraction, authoritative reference normalization, additional AI provider adapters, and project-wide contradiction candidate discovery remain future improvements.

## Deferred v0.2 Knowledge Engine feedback

- `--max-papers` currently limits each provider separately; future behavior should cap aggregate unique papers.
- DOI-less records remain separate; later conservative title/author/year matching should produce manual-review candidates only.
- Providers run sequentially; parallel calls are a future option with bounded timeout and dependable partial-failure handling.

## v0.4 Methodology and Study Design Engine (acceptance complete)

- `methodology/analyzer.py` validates explicit problem inputs, maps the supplied field/type to conservative domain packs, and returns missing information instead of fabricating a gap or question.
- Methodology analysis returns research-type-specific feasibility and sampling checklists, with additional medical, social-science, ML, cybersecurity, or physics checks where relevant. These are prompts for researcher assessment; ResearchOS does not calculate sample sizes or declare a design feasible.
- Statistical guidance includes conditional candidates for common continuous, binary, categorical, count, ordinal, and time-to-event outcomes. It exposes assumptions and missing checks; independent-group methods are withheld unless independence is confirmed. Optional researcher-supplied dataset summaries produce descriptive checks only; the system does not calculate results, power, or adequacy.
- `methodology/store.py` persists editable JSON drafts and requires explicit approval after required fields and source-reviewed evidence claims are supplied.
- The browser methodology panel provides problem analysis, evidence-claim selection, structured design editing, statistical assumption prompts, and an approval checkpoint.
- Optional question refinement sends the explicit problem statement, objectives, research type, and selected reviewed source passages to the configured OpenAI-compatible endpoint. Output is validated against selected claim IDs, carries page/source links, and remains an editable proposal; insufficient evidence can result in no suggestions. Source text is treated as untrusted input.
- v0.4 follow-up scope and limitations are recorded in [v3.md](../v3.md).

## v0.5 Experiment and Reproducibility Engine (complete)

- CSV/JSON datasets are validated and stored content-addressed with SHA-256, heuristic bounded column profiles, immutable original files, and lineage records for transformed outputs. JSON row arrays are decoded one object at a time; duplicate keys and non-finite nested numbers are rejected. Intake is limited to 50 MiB, 1,000,000 rows, 1,000 columns, and 10,000,000 cells.
- Python runs are recorded with source/data hashes, seed, image, limits, duration, bounded logs and artifacts, approved dependency pins, sampled Docker CPU/memory usage, and explicit success/failure. Execution is available only through a local, digest-pinned Docker image whose digest matches a local operator-provided JSON manifest; the manifest hash and Python version are recorded. No host fallback or runtime dependency installation exists. The manifest does not verify installed packages inside the image. Artifacts use a 32 MiB container tmpfs, then retain at most 20 flat regular files and 16 MiB total with hashes. Resource values are sampled while the container runs, so brief peaks may be missed.
- Trusted built-in analysis computes descriptive classification/regression metrics and simple baselines from selected prediction columns, with local confusion-matrix and actual-versus-predicted charts; records retain the dataset hash and make no inferential or generalization claim.
- The digest-pinned Docker runner was exercised on the local daemon. An opt-in live test checked non-root execution, read-only root/workspace/dataset mounts, disabled networking, bounded output artifacts, sampled resource telemetry, and timeout cleanup. Format 4 Python notebooks run sequentially through the same runner; markdown and saved outputs are excluded. See [v4.md](../v4.md) for limits and verification.

## v0.6 Multi-Agent Research Runtime (complete)

- `agents/runtime.py` validates a bounded JSON Schema subset for task inputs and outputs, executes dependency graphs in order, exposes only declared tools through `AgentContext`, and passes only approved/completed dependency outputs downstream.
- Workflow JSON is atomically persisted under `agent_workflows/`. Interrupted tasks become recoverable failures on resume; retries are capped at four attempts. Provider/model identifiers, usage metadata, tool calls, failures, provenance, and researcher review history remain in the task record.
- The local research planner works without credentials. An optional OpenAI-compatible structured-output agent is registered only when provider, model, and key configuration is valid; browser use confirms the outbound request and keeps the output provisional until review.
- Browser and HTTP workflows can create, inspect, resume, edit, approve, reject, and request revisions. See [v5.md](../v5.md) for remaining checks.

## v0.7 Scientific Writing and Publication Engine (complete)

- `writing/store.py` creates typed document drafts from researcher-selected source-linked claims, approved designs, successful runs, and saved evaluation records. Claim references resolve to local PDF IDs, pages, and hashes, with unresolved review status flagged.
- Templates exist for seven document types. Missing research text and absent results remain explicit placeholders; supplied metric values and prediction records are labeled as descriptive or captured output.
- Researchers edit drafts, request revisions, and approve a content hash before export. Markdown, LaTeX, DOCX, and PDF exporters are implemented and structurally tested; see [v6.md](../v6.md) for verification limits.

## v0.8 Research Ecosystem (complete)

- Plugin manifests bind API versions, exact dependency versions, declared permissions, and bounded JSON contracts to a small host-owned adapter set. They cannot import or execute plugin code. Local projects enforce bearer authentication and owner/editor/reviewer/viewer permissions; collections retain typed record references, and revision events attribute changes to local usernames.
- Project, collection, membership, review, revision, and plugin API routes are authenticated. Project and revision listings use bounded cursor pagination. The Python SDK uses the same authenticated routes and surfaces HTTP status on API errors. Existing core routes remain usable without a plugin.
- See [v7.md](../v7.md) for permission and compatibility decisions.

## v0.9 Reliability and Scientific QA (complete)

- `maintenance.py` builds bounded SHA-256 manifests for regular local files, verifies archive membership and hashes before restore, and restores only to a new destination. The health endpoint reports process/storage readiness; the QA report checks JSON readability, dataset/PDF content hashes, and researcher review state without claiming scientific truth or combining checks into a score.
- CLI backup, verification, restore, and quality commands expose these operations. The browser verification view shows health and check-by-check results. Recovery, corruption, and lifecycle checks are covered by offline tests; see [v8.md](../v8.md).

## Roadmap

| Release | Tracked report | Scope |
|---|---|---|
| v0.2 | [v1.md](../v1.md) | Multi-provider discovery foundation |
| v0.3 | [v2.md](../v2.md) | Evidence intelligence and document intelligence |
| v0.4 | [v3.md](../v3.md) | Methodology and study design |
| v0.5 | [v4.md](../v4.md) | Secure experiments and reproducibility |
| v0.6 | [v5.md](../v5.md) | Multi-agent runtime and AI providers |
| v0.7 | [v6.md](../v6.md) | Scientific writing and publication |
| v0.8 | [v7.md](../v7.md) | Plugins, integrations, collaboration |
| v0.9 | [v8.md](../v8.md) | Reliability, security, scientific QA |
| v1.0 | [v9.md](../v9.md) | Integrated stable release |

## v1.0 Integrated Release (complete)

- Project lifecycle summaries reconcile collection references with stored literature runs, source documents, claims, study designs, datasets, experiments, evaluations, and writing drafts. They show researcher review and approval states, stale references, and stage-specific caveats. The lifecycle view does not create scientific conclusions or infer completion from a later-stage record.
- Research run records now include both workflow progress and explicit capability boundaries (researcher review, conditional methods, digest-pinned Docker, and approval-gated writing). The authenticated project lifecycle route and SDK surface this integrated view; a deterministic cross-engine regression exercises run creation through draft export and backup restoration. See [v9.md](../v9.md).

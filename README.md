# ResearchOS

### The Operating System for Scientific Research

**An open-source, evidence-first platform for AI-assisted scientific research.**

ResearchOS brings literature discovery, evidence review, research planning, statistical methodology, controlled experimentation, evaluation, and scientific writing into one structured workspace.

Instead of treating research as a single AI-generated response, ResearchOS organizes it as a traceable workflow where every stage has its own records, provenance, validation, and researcher approval.

[Documentation](docs/architecture.md) · [Roadmap](ROADMAP.md) · [Contributing](#development)

---

## Overview

Scientific research involves multiple disconnected processes: discovering literature, evaluating evidence, designing studies, conducting experiments, analyzing results, and communicating findings.

ResearchOS aims to bring these processes together through a modular, local-first research environment.

Its central principle is simple:

**AI can assist scientific research, but evidence, reproducibility, and human judgment must remain at the center.**

## Core Capabilities

### 1. Literature Discovery & Evidence Management
- Search scholarly literature through OpenAlex and Crossref.
- Aggregate results from multiple literature providers.
- Deduplicate records using normalized DOIs.
- Preserve provider identifiers, source URLs, and retrieval timestamps.
- Import research papers in PDF format.
- Maintain traceable evidence claims linked to reviewed sources.

### 2. AI-Assisted Research Planning
- Convert research requests into structured research plans.
- Organize research into explicit workflow stages.
- Identify missing research inputs.
- Generate conditional domain-specific feasibility and sampling guidance.
- Suggest research questions and hypotheses from selected, reviewed literature claims.
- Keep proposed ideas separate from established findings.

### 3. Research Methodology & Statistical Review
- Review candidate statistical methods for continuous, binary, categorical, count, ordinal, and time-to-event data.
- Provide conditional methodology recommendations.
- Support structured study design creation and approval.
- Link study designs to reviewed evidence claims.

Statistical recommendations require researcher review. The system does not automatically calculate statistical results from unavailable data.

### 4. Controlled Experimentation
- Import and validate CSV and JSON datasets.
- Support datasets up to 50 MiB, 1,000,000 rows, and 10,000,000 cells.
- Generate heuristic column profiles.
- Calculate descriptive classification and regression metrics from supplied predictions.
- Visualize confusion matrices and actual-versus-predicted results.
- Execute Python scripts and notebooks inside a digest-pinned Docker sandbox.

Experiments run with network access disabled, read-only inputs, and resource limits.

### 5. Scientific Writing Engine
Create editable drafts for:

- Research Articles
- Literature Reviews
- Systematic Reviews
- Research Proposals
- Technical Reports
- Conference Papers
- Theoretical Papers

Drafts can use selected reviewed claims, approved study designs, successful experiments, and descriptive evaluations.

Supported export formats:

`Markdown` · `LaTeX` · `DOCX` · `PDF`

Researchers must approve a specific draft revision before export. Missing sections remain explicitly identified rather than being silently fabricated.

### 6. AI Research Agents
- Schema-validated research planning agents.
- Durable task workflows with restart and retry support.
- Provider, model, and usage provenance.
- Explicit tool permissions.
- Human approval, editing, rejection, and revision checkpoints.
- Deterministic local planning without API credentials.
- Optional OpenAI-compatible AI providers.

### 7. Research Workspace
A responsive, local-first browser interface featuring:

- Persistent research conversations.
- Edit-and-resend functionality.
- Voice input where supported.
- PDF importing.
- Configurable input and output styles.
- Research engine navigation.
- Nine-release development progress view.
- Project management and research lifecycle tracking.

---

## Research Workflow

ResearchOS organizes scientific work into a connected lifecycle.

1. **Discovery**: Find relevant scholarly literature.
2. **Evidence Review**: Examine sources and review individual claims.
3. **Study Design**: Develop research questions, hypotheses, and methodology.
4. **Experimentation**: Execute controlled experiments using supplied datasets and code.
5. **Evaluation**: Examine descriptive metrics and experimental outputs.
6. **Writing**: Prepare structured scientific manuscripts.
7. **Project Review**: Track research records, revisions, and lifecycle status.

Each stage maintains its own records and relationships with previous stages.

A completed workflow stage does not automatically establish scientific validity.

---

## Installation

### Requirements

- Python 3.10 or newer.
- Git.
- Docker Desktop (only for sandboxed experiment execution).

Clone the repository:

```bash
git clone https://github.com/Anushrutghi/ResearchOS.git
cd ResearchOS
```

Create a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

For Windows:

```powershell
.venv\Scripts\activate
```

Install ResearchOS:

```bash
python -m pip install -e .
```

## Quick Start

Run your first research request:

```bash
researchos run \
  --topic "Federated Learning for Cybersecurity" \
  --field "Computer Science" \
  --format markdown \
  --output-dir research_runs \
  --max-papers 10
```

ResearchOS generates:

- A structured research run record (`run.json`).
- A Markdown research brief.
- Literature discovery records and source references.

To generate a research plan without contacting external literature providers:

```bash
researchos run \
  --topic "Federated Learning for Cybersecurity" \
  --field "Computer Science" \
  --format markdown \
  --output-dir research_runs \
  --no-search
```

Research run briefs are generated in Markdown. The separate writing engine supports additional export formats.

---

## Browser Workspace

Launch the local ResearchOS interface:

```bash
researchos web
```

Open:

**http://127.0.0.1:8000**

Research runs, conversations, imported PDFs, and datasets are stored locally under `research_runs/`.

Press `Ctrl + C` to stop the server.

### Enable AI Capabilities

ResearchOS supports optional OpenAI-compatible AI providers.

Configure the environment:

```bash
export RESEARCHOS_AI_PROVIDER=openai_compatible
export RESEARCHOS_AI_MODEL=your-model-id
export RESEARCHOS_API_KEY=your-api-key
```

Optionally configure a custom provider endpoint:

```bash
export RESEARCHOS_AI_BASE_URL=your-provider-endpoint
```

Restart the application after configuring these variables.

AI capabilities include research chat, provisional claim suggestions, selected-claim relationship suggestions, and methodology refinement.

Sending a chat message transmits up to 16 recent messages to the configured provider. Selected source text is sent only when the researcher explicitly requests a source suggestion.

Provider network access and charges may apply.

### Gemini Configuration

For a local Gemini setup, copy the example environment file:

```bash
cp .env.example .researchos.env
```

Edit `.researchos.env` and configure:

```env
RESEARCHOS_AI_PROVIDER=openai_compatible
RESEARCHOS_AI_MODEL=your-gemini-model-id
RESEARCHOS_API_KEY=your-api-key
```

Load the configuration:

```bash
set -a
. ./.researchos.env
set +a

researchos web
```

Never commit `.researchos.env`. It is excluded through `.gitignore`.

---

## Experiment Execution

ResearchOS uses an isolated Docker execution environment for Python experiments.

Before enabling execution:

1. Follow the [sandbox image configuration guide](sandbox-image/README.md).
2. Build and publish the approved sandbox image.
3. Configure `RESEARCHOS_SANDBOX_IMAGE` with its digest-pinned reference.
4. Configure `RESEARCHOS_SANDBOX_MANIFEST` with the local manifest path.
5. Start Docker and launch ResearchOS.

The sandbox manifest validates the configured image, Python version, and exact package pins.

Execution safeguards include:

- Network access disabled.
- Read-only input files.
- CPU and memory resource limits.
- Digest-pinned execution images.
- Explicit researcher confirmation.
- No execution through the host Python environment.
- SHA-256 hashes for retained experiment artifacts.

Python notebooks must use Jupyter Notebook format 4 and plain Python cells.

Notebook Markdown and saved outputs are retained as provenance. They are not executed or treated as experimental results.

---

## Projects, API & Plugins

### Project Management

ResearchOS supports authenticated research projects with role-based access.

Available roles:

| Role | Purpose |
|---|---|
| Owner | Project ownership |
| Editor | Project editing |
| Reviewer | Research review |
| Viewer | Read-only access |

Create an API user:

```bash
researchos auth create-user \
  --output-dir research_runs \
  --username alice \
  --display-name "Alice Researcher"
```

The command generates a bearer token. Store it securely because it is printed only once.

Revoke access:

```bash
researchos auth revoke-user \
  --output-dir research_runs \
  --username alice
```

### Python Client

ResearchOS provides a Python client for interacting with authenticated project APIs.

```python
from researchos.client import ResearchOSClient

client = ResearchOSClient(
    "http://127.0.0.1:8000",
    token="YOUR_LOCAL_TOKEN"
)

project = client.create_project("Sleep and Learning")

for item in client.iter_projects(page_size=25):
    print(item["title"])
```

Project collections support typed references to existing ResearchOS records, while project revisions preserve the acting username.

### Plugin Architecture

ResearchOS uses JSON-based plugin manifests with:

- Explicit plugin API versions.
- Semantic versioning.
- Dependency declarations.
- Permission definitions.
- Bounded JSON input/output contracts.

Currently available literature-search adapters:

- `literature.crossref.search`
- `literature.openalex.search`

External requests require explicitly declared permissions. Plugins cannot load arbitrary Python modules or execute arbitrary code.

See [v7.md](v7.md) for plugin contract limitations and remaining ecosystem work.

---

## Reliability & Data Integrity

ResearchOS includes local backup, restoration, and verification capabilities.

Create a backup:

```bash
researchos backup \
  --output-dir research_runs \
  --destination ../researchos-backup.tar.gz
```

Verify it:

```bash
researchos verify-backup ../researchos-backup.tar.gz
```

Restore into a new directory:

```bash
researchos restore \
  ../researchos-backup.tar.gz \
  --destination restored_research_runs
```

Run local quality checks:

```bash
researchos quality --output-dir research_runs
```

These checks examine JSON readability, content hashes, and claim review state. They do not determine whether scientific claims are true or whether research is scientifically valid.

---

## Research Integrity

ResearchOS is designed around transparent and traceable scientific workflows.

Its integrity principles include:

- Preserve literature provider identifiers and source provenance.
- Record search completeness and individual provider outcomes.
- Distinguish unavailable providers from successful searches with zero results.
- Separate discovery metadata from reviewed scientific evidence.
- Keep hypotheses and proposed experiments distinct from established findings.
- Require researcher review of AI-generated suggestions.
- Calculate prediction metrics only from supplied dataset rows.
- Preserve experiment provenance and artifact hashes.
- Never treat AI-generated text as verified scientific findings.

**ResearchOS is not an autonomous laboratory or an automated scientific authority.** It is a research infrastructure platform designed to support researchers while keeping scientific decisions under human control.

---

## Security Considerations

ResearchOS is intended to bind to `127.0.0.1` by default.

The built-in server does not provide TLS. Bearer authentication currently protects project and plugin routes, not every legacy browser API.

Therefore:

- Do not expose the built-in server directly to public or untrusted networks.
- Keep API keys and local credentials outside version control.
- Protect backup archives because they contain local user and token-hash records.
- Use a trusted reverse proxy with transport security and access control for every route when remote access is required.

---

## Development

Run the test suite:

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

If your system uses `python3`:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

### Project Documentation

- [Architecture](docs/architecture.md)
- [Development Roadmap](ROADMAP.md)
- [Sandbox Image Guide](sandbox-image/README.md)
- [Plugin Limitations](v7.md)

---

## Current Limitations

ResearchOS is actively evolving. Some capabilities are still experimental or incomplete.

Current limitations include:

- Literature abstracts may be unavailable.
- Search results are discovery leads, not proof of scientific validity.
- AI-generated claims and relationships remain provisional.
- No automated batch contradiction analysis.
- PDF table extraction and reference normalization are heuristic.
- Statistical methodology suggestions require researcher review.
- Prediction metrics do not establish model generalization or statistical significance.
- Experiment execution requires a configured Docker sandbox.
- Polished PDF/DOCX output and domain-specific review capabilities remain under development.

See [ROADMAP.md](ROADMAP.md) and the corresponding release reports for development progress and acceptance criteria.

---

## Contributing

ResearchOS is an open-source project, and contributions are welcome.

Areas of interest include:

- Literature discovery and evidence management.
- Research methodology and statistical review.
- AI-assisted scientific workflows.
- Reproducible experimentation.
- Scientific document generation.
- Security and data integrity.
- Testing and documentation.

Please open an issue to discuss significant changes before submitting a pull request.

---

## License

See the repository's license file for licensing terms.

---

**ResearchOS**

*Making scientific research more structured, transparent, and reproducible.*

Built with a commitment to evidence, reproducibility, and responsible AI.

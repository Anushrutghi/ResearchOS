# ResearchOS roadmap and version tracking

This roadmap maps the nine tracked deliveries from the current v0.2 baseline through v1.0. Each root `vN.md` file is the milestone report: completed work, feedback/known issues, and remaining work. A milestone is marked complete only after its acceptance checks pass.

| Tracking file | Release | Focus | Status |
|---|---|---|---|
| [v1.md](v1.md) | v0.2 | Provider-neutral discovery foundation (OpenAlex + Crossref) | Complete |
| [v2.md](v2.md) | v0.3 | Evidence Intelligence Engine | Complete (core acceptance) |
| [v3.md](v3.md) | v0.4 | Methodology and scientific study design | Complete (advanced statistical methods remain follow-up work) |
| [v4.md](v4.md) | v0.5 | Experiment and reproducibility | Complete |
| [v5.md](v5.md) | v0.6 | Multi-agent research runtime | Complete |
| [v6.md](v6.md) | v0.7 | Scientific writing and publication | Complete |
| [v7.md](v7.md) | v0.8 | Plugins, integrations, collaboration | Complete |
| [v8.md](v8.md) | v0.9 | Reliability, security, scientific quality | Complete |
| [v9.md](v9.md) | v1.0 | Integrated ResearchOS release | Complete |

The browser workspace should show these same statuses. An interface control or roadmap entry does not mean its underlying engine is implemented.

## Delivery rules

- Preserve working capabilities while adding new engines behind clear interfaces.
- Keep research content traceable to source records and distinguish verified results from proposed or generated material.
- For each completed release, update its `vN.md`, tests, user documentation, and the browser roadmap status.
- Keep future release files visible with their remaining acceptance criteria.
- Do not mark a milestone complete while its listed critical acceptance checks fail.

## Cross-version deferred Knowledge Engine feedback

- **Aggregate paper limit:** `--max-papers` currently applies per provider. Later it should cap the final number of unique papers across providers.
- **DOI-less duplicates:** Keep these separate for now. Later, generate conservative title/author/year match candidates for manual review; do not merge automatically based on title alone.
- **Sequential provider calls:** Search providers currently run sequentially. Parallel calls may improve latency if bounded timeouts, cancellation, deterministic outcome recording, and partial-failure handling remain reliable.

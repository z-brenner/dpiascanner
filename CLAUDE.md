# Katz: project charter

Every session working in this repository reads this file first. It is the design contract. If a change would violate it, stop and raise the conflict instead of working around it.

## 1. Purpose

Katz clones a GitHub repository, builds a deterministic data-flow graph of personal data (sources, transformations, sinks, retention points), classifies every node and edge with a calibrated decision model, and generates a Data Protection Impact Assessment under GDPR Article 35 plus a CCPA/CPRA mapping.

Every claim in the report must be traceable to a graph node or edge with file path and line number. If a claim cannot be traced, it does not appear in the report.

## 2. Design principles (non-negotiable)

- **Ground truth comes from program analysis, not from language models.** Parsing and taint tracking are deterministic. An LLM never decides whether data flows; it only drafts narrative from structured findings, and every sentence it drafts cites a finding id.
- **The decision model answers fixed, typed questions with calibrated probabilities.** The decision model (Jev, from TypeSafe AI) never generates free text. All question sets are versioned. Same input plus same question set version must produce identical outputs.
- **Confidence is a first-class output.** Any decision below the configured threshold is reported as unresolved with its evidence, never guessed.
- **Every finding is reproducible.** Each finding stores the commit SHA, the question set version, and the raw decision output so it can be reproduced and diffed later.
- **Secrets never leave the worker.** Secrets discovered in a repo are never stored, logged, or sent to any model. Redact before anything leaves the worker.
- **The decision layer sits behind an interface (`DecisionProvider`).** A deterministic stub and a local classifier must be swappable for the hosted Jev client.

## 3. Architecture

| Path | Role |
|---|---|
| `apps/api` | FastAPI. Jobs, findings, reports, GitHub App webhooks. |
| `apps/worker` | Python. Clone, parse, graph, classify, verify, report. Each job runs in its own process (or container) with a timeout and a fresh directory that is deleted afterwards; outbound HTTP is limited to an allowlist (GitHub, the decision provider, the package registries). Repository code runs only in the dynamic-verification sandbox. |
| `apps/web` | React + TypeScript + Vite. Repo picker, run status, report viewer, diff viewer. Deployed as a Cloudflare Worker that serves the build and proxies `/api` to the API (`docs/deploy-cloudflare.md`). |
| `packages/analysis` | tree-sitter parsing, Semgrep rule packs, graph builder (networkx), dependency registry integration. |
| `packages/decisions` | `DecisionProvider` interface, Jev client, stub provider, question set definitions and versions. |
| `packages/report` | DPIA assembler, GDPR Art. 35 and ICO template mapping, CCPA/CPRA mapping, cited narrative, JSON, Markdown, HTML, and DOCX renderers. |
| `packages/registry` | Curated third-party SDK registry with documented data behaviors and endpoints. |
| `packages/platform` | Shared by the API and worker: settings, database models, run queue, GitHub App client, token encryption, egress allowlist, run persistence. |
| `fixtures/` | Synthetic repositories with planted canary flows and a manifest of expected findings. |

Storage: Postgres for jobs, graphs, decisions, and reports. Redis for the job queue.

Dependency direction (no cycles): `registry` ← `analysis` ← `report`; `decisions` ← `report`; everything ← `worker` ← `api`. Where a lower package needs a higher one (for example the registry's dependency resolver needs the analyzer), the caller injects it.

## 4. Data model summary

- **Repo**
- **Run**: repo, sha, ref, status, question_set_version
- **Node**: run, kind in `source | transform | sink | retention`, file, line_start, line_end, symbol, snippet_hash
- **Edge**: run, from_node, to_node, evidence
- **Decision**: run, target_id, target_type, question_id, answer, probability, distribution_json, provider, provider_version
- **Finding**: run, category, severity, node_ids, edge_ids, decision_ids, statute_refs
- **Report**: run, format, content

## 5. Coding standards

- Python 3.12, type hints everywhere, `ruff` and `mypy --strict`, `pytest`.
- TypeScript `strict`.
- Conventional commits (`feat(analysis): ...`, `fix(report): ...`, `test(fixtures): ...`).
- Every package has a README describing its inputs and outputs.
- No feature is done without a test against `fixtures/`.

## 6. Definition of done (whole project)

- The pipeline catches 100 percent of planted canary flows in `fixtures/` and reports zero false positives on the clean fixture.
- A full run on a 20k-line Python repo completes in under ten minutes on a single worker.

## 7. Working in this repo

- `make install` syncs the uv workspace and the pnpm workspace. `make lint`, `make test`, `make run`. `make test-dynamic` runs the fixtures against the mock server; `make test-docker` runs the Docker sandbox (opt-in, builds images). `make benchmark-gate` enforces the definition of done on the fixtures (CI runs it); `make benchmark` regenerates `benchmarks/RESULTS.md`.
- Python packages live under `packages/<name>/src/lantern_<name>/` and `apps/<name>/src/lantern_<name>/`, with tests in `<package>/tests/`. pytest runs in importlib mode, so test module names may repeat across packages.
- **Naming.** The product is Katz. It was first called Lantern, and code identifiers keep that name:
  - the `lantern_*` Python packages and their `lantern-*` distributions;
  - the `LANTERN_*` environment variables;
  - the per-repository `lantern.yml`;
  - the `lantern.*/v*` schema ids;
  - container paths and image names;
  - user agents;
  - the planted fixture values, such as "Lantern Canary".

  Use Katz in anything a person reads. Rename identifiers only as one deliberate change, never piecemeal.
- **Fixture first.** When a real repository exposes an analyzer gap, add the pattern to a fixture and its manifest first, watch the test fail, then fix the analyzer. Never tune a fixture to make the analyzer pass.
- The `StubProvider` is an oracle tuned to the fixtures. Tests that use it prove plumbing, not classification quality. Classification quality is measured only by the calibration harness against a real provider.
- When a session runs out of context, the next one starts with: "Read CLAUDE.md and the README in the package you are working in, then continue from the failing tests."

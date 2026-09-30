# Katz

A privacy impact assessment tool that runs against real source code instead of a product manager's memory. Katz builds a deterministic data-flow graph of personal data in a GitHub repository, classifies it with a calibrated decision model, and produces a GDPR Article 35 DPIA and a CCPA/CPRA mapping in which every claim cites a file and line.

Katz was first called Lantern. Code identifiers keep that name: the `lantern_*` packages, the `LANTERN_*` settings, and the per-repository `lantern.yml`.

Start with [CLAUDE.md](CLAUDE.md), the design contract.

```bash
make install   # uv + pnpm
make lint
make test
make run       # postgres + redis via docker compose, api, worker, web
```

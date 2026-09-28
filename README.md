# Lantern

A privacy impact assessment tool that runs against real source code instead of a product manager's memory. Lantern builds a deterministic data-flow graph of personal data in a GitHub repository, classifies it with a calibrated decision model, and produces a GDPR Article 35 DPIA and a CCPA/CPRA mapping in which every claim cites a file and line.

Start with [CLAUDE.md](CLAUDE.md), the design contract.

```bash
make install   # uv + pnpm
make lint
make test
make run       # postgres + redis via docker compose, api, worker, web
```

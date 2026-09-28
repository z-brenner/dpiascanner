# lantern-worker

Consumes run jobs: clone at SHA, analyze, resolve registry and dependencies, classify, optionally verify dynamically, and generate reports.

**Inputs:** run jobs from Redis.
**Outputs:** graph, decisions, findings, and reports persisted to Postgres. The clone is deleted after each run.

Status: skeleton. Implemented in Prompts 5, 6, and 8.

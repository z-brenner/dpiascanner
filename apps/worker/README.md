# lantern-worker

Runs jobs: clone at SHA, analyze, classify, optionally verify dynamically, and generate
reports.

**Inputs:** a repository checkout (or a run job from Redis, once the API lands) and a
`DecisionProvider`.
**Outputs:** the graph, decision rows (with raw provider output), findings, and reports,
persisted through a `RunStore`.

`pipeline.run_pipeline(repo, commit, PipelineConfig(provider))` runs, in order:

1. `analyze_repo` to build the graph;
2. `build_targets` to build redacted, bounded decision states;
3. `classify`, one batched provider call over all targets, with each state checked by
   the secrets `payload_guard` first;
4. `assemble_findings`.

`FileRunStore` persists runs to disk for local use and tests; the API adds a SQL store.

# lantern-worker

Runs jobs: clone at SHA, analyze, classify, optionally verify dynamically, and generate
reports.

**Inputs:** a repository checkout (or a run job from Redis, once the API lands) and a
`DecisionProvider`.
**Outputs:** the graph, decision rows (with raw provider output), findings, and reports,
persisted through a `RunStore`.

`pipeline.run_pipeline(repo, commit, PipelineConfig(provider))` runs, in order:

1. `analyze_repo` to build the graph (stages `parse`, `graph`, `registry`);
2. `build_targets` to build redacted, bounded decision states;
3. `classify` (stage `classify`), one batched provider call over all targets, with each state
   checked by the secrets `payload_guard` first. With `PipelineConfig.incremental`, only
   targets in the changed files and their graph neighbors are classified; the rest reuse the
   base run's decisions when their state hash is unchanged;
4. dynamic verification (stage `verify`), when `PipelineConfig.dynamic` is set (see
   [`dynamic/README.md`](src/lantern_worker/dynamic/README.md)): run the repository in the
   Docker sandbox with canary values and mark each sink `verified`, `inferred`, or
   `observed-unexpected`;
5. `assemble_findings`, which carries each sink's verification status as evidence.

`FileRunStore` persists runs to disk for local use and tests. Production runs go through
`jobs.process_run` and the database (`lantern_platform.storage`).

## Jobs

`python -m lantern_worker serve` consumes the Redis queue. Each run is processed by
`python -m lantern_worker run-job RUN_ID` in its own process (`LANTERN_JOB_ISOLATION=process`)
or a fresh container from `LANTERN_WORKER_IMAGE` (`container`: read-only root, capped tmpfs,
no capabilities, secrets passed by name so they never appear in a command line). The
supervisor kills a job at `LANTERN_RUN_TIMEOUT_S` and records the timeout against the stage
that was running.

`jobs.process_run`:

1. **clone**: `fetch.GitFetcher` fetches exactly the run's SHA at depth 1 into a fresh
   temporary directory, with the installation token passed as an HTTP header through git's
   environment, and fails if the checkout exceeds `LANTERN_CLONE_MAX_MB`. The URL must pass
   the egress allowlist.
2. **parse, graph, registry, classify, verify**: `run_pipeline`. For pull requests, the run
   is incremental against the latest completed base-branch run: targets in the changed files
   and their one-hop graph neighbors (`incremental.neighbor_files`) are classified, the rest
   reuse base decisions whose state hash is unchanged.
3. **report**: code text is scrubbed, the report is rendered in all four formats, and every
   row is written in one transaction. The run is `completed` with all of its results or
   `failed` with none, and the failing stage and redacted error are recorded.
4. For pull requests: the check run is completed and the single PR comment is created or
   updated. A GitHub failure at this point is recorded in the run summary and does not fail
   the run.

The clone directory is deleted in every case. Runs can be cancelled; a running job stops at
the next stage boundary. Dynamic verification needs the Docker sandbox; when no Docker
daemon is reachable, the `verify` stage is recorded as skipped.

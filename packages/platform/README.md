# lantern-platform

The service layer shared by `apps/api` and `apps/worker`.

**Inputs:** environment settings (`config.Settings.from_env`).
**Outputs:** database access, the run queue, an authenticated GitHub client, and run
persistence.

| Module | Contents |
|---|---|
| `config.py` | Settings from the environment |
| `db.py` | SQLAlchemy models (runs, stages, nodes, edges, decisions, findings, reports, users, sessions, installations, PR comments, webhook deliveries) |
| `queue.py` | `RedisQueue` and `InMemoryQueue` |
| `github.py` | App JWT, installation tokens cached until five minutes before expiry, webhook signature check, repository reference parsing, REST calls |
| `tokens.py` | Installation tokens cached in the database, encrypted |
| `crypto.py` | Fernet encryption for tokens at rest; sessions stored by SHA-256 |
| `egress.py` | Outbound host allowlist enforced on every HTTP client and on clone URLs |
| `storage.py` | Save and load a run's graph, decisions, findings, and reports; `scrub` removes code text other than node snippets |
| `pr.py` | The PR comment (one per pull request, found by id or hidden marker) and check run output |

Retention: after a run, the only repository text in the database is each node's snippet,
which the analyzer bounds and redacts. Evidence step text and other code excerpts are blanked
by `storage.scrub` before anything is stored, including the stored reports.

# lantern-analysis

Deterministic program analysis. No model calls happen here.

**Input:** a checked-out repository directory (plus the SDK registry).
**Output:** a `DataFlowGraph` (schema `lantern.graph/v1`, serializable to JSON and to a
networkx `DiGraph`) of personal-data **sources**, **transforms**, **sinks**, and
**retention** points, the **flows** between them with file-and-line evidence for every
propagation step, attached **config**, and a **summary** with counts and coverage.

```bash
uv run python -m lantern_analysis path/to/repo --out graph.json --commit <sha>
```

```python
from lantern_analysis.analyze import analyze_repo

graph = analyze_repo("path/to/repo", commit="<sha>")
```

## Pipeline

1. **Discover** files (`repo.py`). Tests, vendored, generated, and build output are
   excluded by default. `lantern.yml` → `analysis.exclude` adds globs.
2. **Parse** with tree-sitter into a small IR (`parsing/python.py`, `parsing/typescript.py`,
   `ir.py`). Each module carries its functions, classes, methods, imports, call sites,
   assignments, attribute accesses, and literals, all with spans.
3. **Match rules** with Semgrep (`rules.py`, `rules/{python,typescript}/*.yaml`), offline
   with metrics off. Every match has a stable rule id (`python.sink.orm.sqlalchemy-write`)
   and a kind: source, sink, or transform.
4. **Resolve** (`project.py`): imports (including `src/` and `backend/` source roots),
   scopes, light type inference from annotations, constructors, and module-level object
   literals, and call targets, including dependency-injected instances whose type is
   statically known (constructor parameters, `Depends(...)` providers, DI containers).
5. **Detect** (`detect.py`): route handlers and other entry points; request-body, form,
   header, cookie, env, upload, and ORM-read sources (lexicon-gated); sinks from rules and
   from the registry; transforms.
6. **Propagate taint** (`taint.py`) over a value-flow graph with field-sensitive label
   paths, call-string context sensitivity (depth limit 12 by default), exception channels
   (`raise` → registered exception handlers and `except`), and request containers that
   turn into sources when a personal field is read from them.
7. **Evidence scanners:** reachability from entry points (`reach.py`), vendor mitigation
   hooks such as Sentry `before_send` and what they scrub (`mitigations.py`), retention
   evidence such as TTL fields, deletion jobs, and crontab schedules (`retention.py`), and
   cross-file config with conflict flags (`configres.py`).
8. **Build the graph** (`builder.py`). Node and edge ids are hashes of stable properties,
   so the same code gives the same ids across runs. Snippets are bounded and redacted
   (`secrets.py`) before they enter the graph.

## Semantics worth knowing

- **Lexicon matches are hints, not findings** (`lexicon.yaml`). Weak terms (`name`,
  `condition`, `address`) need a context word from the owning class, function, route, or
  file (a `Product.name` is not personal; a `HealthRecord.condition` is). Internal linkage
  ids (`user_id`) are not tracked unless `track_internal_ids` is set.
- **Unresolved, never guessed.** A dict read with a non-literal key, `getattr` with a
  computed name, `eval`, or a call through a callback parameter keeps the taint, marks the
  step unresolved with a note, and marks everything downstream inherited-unresolved.
- **Unreachable flows are kept**, flagged `reachable: false`.
- **Library sinks do not propagate their return value.** An HTTP response or an SDK
  object is not the data that was sent.
- The coverage metric is `paths_with_unresolved_step / tainted_paths`.

## Known gaps (found on real repositories, not yet in fixtures)

Per the project rule, each of these gets a fixture flow first, then an analyzer change.

- SMTP and generic email libraries (`smtplib`, `emails`, `fastapi-mail`, `nodemailer`) are
  not sinks yet.
- FastAPI dependency aliases (`CurrentUser = Annotated[User, Depends(...)]`) are not treated
  as ORM-row sources.
- SQLModel `table=True` classes are recognized only through `__tablename__` or a known base.
- Server-rendered templates, GraphQL resolvers, Django class-based views, and NestJS
  decorators have no source rules yet.
- Analysis within a function is flow-insensitive, so a later `x = None` does not kill taint.

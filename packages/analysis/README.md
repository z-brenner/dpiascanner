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
8. **Expected destinations** (`endpoints.py`): every network sink gets
   `attrs["endpoints"]`, the hosts it should reach, from the registry entry, URL literals in
   its arguments (following module constants), attached config values, code defaults of
   environment reads (`os.environ.get("X", "https://...")`, `process.env.X ?? "https://..."`),
   and profiled dependency source. Dynamic verification matches observed requests to these.
9. **Build the graph** (`builder.py`). Node and edge ids are hashes of stable properties,
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
- **Mutator methods carry taint into their receiver** when it is a local or module
  variable, from a short list (`taint.py`, `MUTATOR_METHODS`): element adders such as
  `append` and `add`, merges such as `update`, and content setters such as
  `EmailMessage.set_content` and `add_attachment`. Other library calls pass taint to their
  result only, so a tainted request does not taint the client.
- **ORM reads** are `session.query(Model)`, `session.get(Model, ...)`, Django managers, and
  `select(Model)` passed to `execute`, `scalars`, `scalar`, or SQLModel's `exec`, chained
  (`.where(...)`) or bound to a variable first. A SQLModel class is a table only when it is
  declared with `table=True`; its non-table bases and siblings are schemas.
- **SMTP** (`smtplib.SMTP`, `SMTP_SSL`, `LMTP`, `aiosmtplib`) is a sink of family `email`: a
  third-party disclosure to the mail relay, classified as `communications`.
- The coverage metric is `paths_with_unresolved_step / tainted_paths`.

## Known gaps (found on real repositories)

Per the project rule, each of these gets a fixture flow first (in `fixtures/gaps-python`,
marked `known_gap`), then an analyzer change. That fixture is part of the CI gate except for
flows still marked `known_gap`. SMTP (G01) and SQLModel reads (G03, G04) were fixed in
z-brenner/dpiascanner#2.

- Email libraries other than `smtplib` and `aiosmtplib` (`emails`, which
  full-stack-fastapi-template uses, `fastapi-mail`, `nodemailer`) are not sinks yet.
- The SMTP server's host is not resolved statically (it is usually a constructor argument
  read from settings), so an `email` sink has no expected endpoints and the report names it
  "an SMTP mail server".
- An SMTP client defined on `self` in one method and used in another is not matched.
- FastAPI dependency aliases (`CurrentUser = Annotated[User, Depends(...)]`) are not treated
  as ORM-row sources. A session injected the same way does work as an ORM sink (G02 passes).
- Server-rendered templates, GraphQL resolvers, Django class-based views, and NestJS
  decorators have no source rules yet.
- Analysis within a function is flow-insensitive, so a later `x = None` does not kill taint.

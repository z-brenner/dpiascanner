# lantern-api

FastAPI service: GitHub App installation flow, repositories, runs, findings, reports, diffs,
and GitHub webhooks.

**Inputs:** HTTP requests from the web app (session cookie) and GitHub webhooks.
**Outputs:** runs enqueued to Redis; JSON responses and rendered reports read from Postgres.

`make api` runs it with uvicorn (`lantern_api.main:app`, built from the environment; see
`docs/github-app.md` for the variables). Tests build it with `create_app(Services.build(...))`
against SQLite, an in-memory queue, and a fake GitHub.

| Method and path | Purpose |
|---|---|
| `POST /auth/github/callback` | Installation flow: exchange `code`, record installations, set the session cookie |
| `GET /auth/me`, `POST /auth/logout`, `GET /installations` | Session and installations |
| `GET /repos?q=` | Repositories reachable through the user's installations, filtered server side |
| `POST /repos/resolve` | Normalize a pasted URL or `owner/repo`: installed, public, inaccessible (with a reason and install link), or invalid |
| `POST /runs` | Create a run for owner, repo, ref (default branch if omitted), and options (languages, dynamic verification, dependency depth). Rate limited per user (`LANTERN_RUNS_PER_HOUR`) |
| `GET /runs/{id}` | Status, stage tracker (clone, parse, graph, registry, classify, verify, report), coverage, error with the failing stage |
| `POST /runs/{id}/cancel` | Cancel a queued run, or ask a running one to stop at the next stage |
| `GET /runs/{id}/findings` | Filters: `category`, `severity`, `status`, `verification` (repeatable), `q`; paginated |
| `GET /runs/{id}/findings/{fid}` | Nodes with snippets and links at the SHA, edges with evidence steps, decisions with probabilities |
| `GET /runs/{id}/report?format=json\|md\|html\|docx` | The stored report (`download=true` for an attachment) |
| `GET /repos/{owner}/{repo}/runs`, `GET /runs/{a}/diff/{b}` | Run history and findings diff |
| `POST /webhooks/github` | Signed webhooks: installation, push to the default branch (full run), pull request opened/reopened/synchronize (incremental run, check run, one PR comment) |

Access: a user sees runs they created and runs of repositories in their installations, and,
with `LANTERN_RETENTION_HOURS` set, only runs younger than the retention period. The API
deletes expired data when it starts and every hour (`lantern_platform.retention`), and
`/config` reports whether deletion is running, so the web app states the promise only then.
Sessions are stored by hash; GitHub tokens are encrypted with `LANTERN_TOKEN_KEY`.
Browser requests are protected by `SameSite=Lax` cookies and JSON-only bodies.

The public demo's switches:
- `LANTERN_PUBLIC_REPOS_ONLY`: the resolver, `POST /runs`, and webhooks refuse private
  repositories.
- `LANTERN_PROXY_SECRET`: every path except `/healthz` requires the
  `X-Katz-Proxy-Secret` header. It is for an API whose hostname is public, as on Render
  (`docs/deploy-render.md`).

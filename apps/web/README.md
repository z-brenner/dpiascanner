# @lantern/web

React, TypeScript, Vite, and Tailwind front end for the Lantern API. Plain screens, no
marketing pages.

**Inputs:** the Lantern API (`/api/*`, proxied to `localhost:8000` in development; set
`VITE_API_BASE` when the API is elsewhere). The session is an HttpOnly cookie set by the API.
**Outputs:** none of its own; everything is read from and written through the API.

| Route | Screen |
|---|---|
| `/` | Connect: install the GitHub App, see connected installations |
| `/auth/github/callback` | Where GitHub sends the browser after installation; posts `code` and `installation_id` to the API |
| `/repos` | Server-side repository search, and a paste-or-type field that resolves any GitHub URL or `owner/repo`, with an install link when the app cannot reach it. Run opens the options panel (branch, languages, dynamic verification, dependency depth) |
| `/runs/:id` | Stage tracker (clone, parse, graph, registry, classify, verify, report) polled every two seconds, coverage numbers as they arrive, cancel |
| `/runs/:id/report` | The DPIA from the report JSON, a findings table filtered by category, severity, resolved or unresolved, and verified or inferred, and a drawer with the full path, snippets, evidence steps linked to GitHub at the SHA, and decisions with probabilities. Download as Markdown, HTML, DOCX, or JSON |
| `/repos/:owner/:repo/diff` | Pick two runs and see new, resolved, and changed findings |
| `/settings` | Threshold, question set, registry version, decision provider, and limits in use |

`pnpm --filter @lantern/web dev`, `test` (Vitest with Testing Library: the repository
resolver field, the findings filters, the stage tracker), `lint` (tsc), `build`.

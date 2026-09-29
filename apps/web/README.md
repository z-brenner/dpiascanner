# @katz/web

React, TypeScript, Vite, and Tailwind front end for the Katz API. Plain screens, no
marketing pages.

**Inputs:** the Katz API (`/api/*`, proxied to `localhost:8000` in development; set
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
| `/about` | What Katz is and is not (reports are not legal advice); in the demo build, what the demo stores, who processes it, and how to have it deleted |

`pnpm --filter @katz/web dev`, `test` (Vitest with Testing Library: the repository
resolver field, the findings filters, the stage tracker; and the Cloudflare Worker), `lint`
(tsc, for the app and the Worker), `build`.

## Cloudflare

In production the build is served by a Cloudflare Worker (`wrangler.jsonc`, `worker/`):
static assets with a single-page-app fallback and the headers in `public/_headers`, and
`/api/*` proxied to the API origin in `API_ORIGIN` with the prefix removed, so the browser sees
one origin. Locally: `pnpm --filter @katz/web build`, then
`pnpm --filter @katz/web exec wrangler dev --var API_ORIGIN:http://localhost:8000` with the API
on port 8000. `cf:deploy` builds and deploys.

`build:cf` builds the public demo (Vite mode `cloudflare`, `.env.cloudflare`). It adds a
banner, a "Before you install" box on Connect, and an About page that states what the demo
stores and who processes it (`src/demo.ts`, `src/components/DemoNotice.tsx`,
`src/pages/AboutPage.tsx`). A plain `build` is for self-hosting and shows none of it. The full setup,
including the API host, the tunnel, and Access, is in `docs/deploy-cloudflare.md`.

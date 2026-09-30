# Deploying the backend on Render

Instead of a server running Docker Compose behind a Cloudflare Tunnel
(`docs/deploy-cloudflare.md`, steps 1–3), the API, the worker, Postgres, and the queue can run
on Render from `render.yaml`. The web app still runs on Cloudflare Workers.

```
browser ──> Cloudflare Worker "katz-web" ──/api/* + X-Katz-Proxy-Secret──> Render
                                                   katz-api (web service, Docker)
                                                   katz-worker (background worker)
                                                   katz-db (Postgres) · katz-queue (Key Value)
```

## What changes compared with your own server

| | Own server (Compose) | Render |
|---|---|---|
| Keeping the API private | Cloudflare Tunnel plus Access: no public hostname | The `*.onrender.com` hostname is public. IP allowlists need the $499/mo Scale tier, and could not single out a Cloudflare Worker anyway. Instead, the API answers only requests carrying the shared secret `LANTERN_PROXY_SECRET`, which the Worker sends. `/healthz` stays open for Render's health check. |
| Dynamic verification | Possible, with the worker on the host and Docker access | Off. Render gives services no Docker daemon and no `NET_ADMIN`, which the sandbox needs. Static analysis is unaffected. |
| Job isolation | Process or container | Process (`LANTERN_JOB_ISOLATION=process`) |
| Disk for clones | Yours | Ephemeral, and reportedly about 2 GB of `/tmp`. The default `LANTERN_CLONE_MAX_MB=500` fits. |

## Cost

The plans in `render.yaml` were checked in September 2026.

| Resource | Plan | Price | Cheaper option and its cost |
|---|---|---|---|
| `katz-api` | `0.5c-512mb` | $7/mo | `free`: it sleeps after 15 idle minutes and takes about a minute to wake. The first visitor waits, and GitHub webhooks, which time out after 10 s, fail while it sleeps. |
| `katz-worker` | `0.5c-512mb` | $7/mo | None: workers have no free plan. Measured on a 14k-line repository, the analyzer peaked at 70 MB and Semgrep at 130 MB. |
| `katz-db` | `0.1c-256mb` | $6/mo | `free`: deleted 30 days after creation, with no backups. |
| `katz-queue` | `free` | $0 | Already free. It is in memory, so a restart loses queued runs. `256mb` ($10) persists. |

## Steps

1. **GitHub App.** Create it as in `docs/github-app.md`. Point every URL at the site, as in
   `docs/deploy-cloudflare.md` step 5; the webhook URL is `https://<site>/api/webhooks/github`.
2. **Blueprint.** In Render, go to **New → Blueprint** and pick this repository. Render reads
   `render.yaml` and asks once for the values marked `sync: false`:
   - `LANTERN_WEB_URL`: the site's origin, for example `https://katz.example.com`;
   - `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_PRIVATE_KEY` (the PEM, with newlines or
     `\n`), `GITHUB_WEBHOOK_SECRET`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`.

   The worker copies the App settings and `LANTERN_WEB_URL` from the API. After the first
   deploy, confirm they appear under the worker's **Environment** tab.
3. **Connect the Worker to the API.**
   - Copy `LANTERN_PROXY_SECRET` from the `katz-shared` environment group.
   - In `apps/web`, run `pnpm exec wrangler secret put API_PROXY_SECRET` and paste it.
   - Set `API_ORIGIN` to `https://katz-api.onrender.com`, or to your custom domain for the
     API. Use the `LANTERN_API_ORIGIN` repository variable, or `--var` on a manual deploy
     (`docs/deploy-cloudflare.md` step 4).
4. **Name Render as a processor.** Set the `LANTERN_BACKEND_HOST` repository variable to
   something like `Render Services, Inc. (Frankfurt, Germany)`, so the site's About page
   names it. Render is a US company, so the transfer notes in `docs/deploy-cloudflare.md`
   apply to it as they do to Cloudflare. Check Render's current data processing terms. Set
   `LANTERN_BACKUP_DAYS` to `7` too (see "Backups outlive deletion" below).
5. **Deploy the Worker** with `build:cf`, as in `docs/deploy-cloudflare.md` step 4.

## Check it

```bash
curl -s https://katz-api.onrender.com/healthz            # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' https://katz-api.onrender.com/config   # 404: secret required
curl -s https://<site>/api/config | python -m json.tool      # through the Worker
```

The last command should show:
- `"public_repos_only": true`;
- `"retention": {"hours": 72, "active": true, ...}`.

`active` turns true once a sweep has run, and the API sweeps as it starts.

| Symptom | Cause |
|---|---|
| Every `/api` call answers 404 `not found` | The Worker's `API_PROXY_SECRET` differs from the API's `LANTERN_PROXY_SECRET`, or it is missing |
| The site waits about a minute, then works | The API is on the free plan and was asleep |
| Runs stay queued | The worker is not running, or it cannot reach Key Value: check the worker's logs and region |
| The About page says deletion "has not run recently" | No sweep in three hours: the worker is down and the API has not restarted. Check the worker. |

## The demo settings

The `katz-shared` group in `render.yaml` sets:

| Variable | Value | Effect |
|---|---|---|
| `LANTERN_PUBLIC_REPOS_ONLY` | `1` | Several layers enforce it: <ul><li>the API refuses private repositories, whether pasted or installed;</li><li>webhooks from private repositories start nothing;</li><li>the worker refuses private runs and clones without the installation token, so a private repository cannot be fetched even by mistake.</li></ul> |
| `LANTERN_RETENTION_HOURS` | `72` | <ul><li>Each scan, with its findings, reports, and code excerpts, is deleted 3 days after it was created.</li><li>A user who has not signed in for 3 days is deleted with their scans.</li><li>The worker sweeps hourly, and the API sweeps when it starts and hourly after that.</li><li>The API never serves a scan older than 3 days, even before a sweep.</li><li>The site states the 3-day promise only while sweeps are running.</li></ul> |
| `LANTERN_SESSION_TTL_HOURS` | `72` | Sign-ins last 3 days, matching the retention period |
| `LANTERN_DECISION_PROVIDER` | `stub` | Classification runs on the server, so no model receives code |

**Backups outlive deletion.** A sweep deletes from the live database. Render keeps paid
databases recoverable to any point in the last 3 days on a Hobby workspace, or 7 on Pro and up,
and keeps a logical backup you export for 7 days
([Render docs](https://render.com/docs/postgresql-backups)). So a deleted scan can survive in
backups for up to 7 more days. Set the repository variable `LANTERN_BACKUP_DAYS` to `7` (or `3`
on Hobby, if you never export backups by hand) and the About page says so. If you ever restore a
backup, the API still hides scans past their date and the next sweep deletes them again.

To run the same backend privately, for your own team: remove `LANTERN_PUBLIC_REPOS_ONLY`, set
`LANTERN_RETENTION_HOURS` to what your policy says (0 keeps everything), and build the web
app with `pnpm run build`, which shows no demo notice.

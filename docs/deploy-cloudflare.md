# Deploying on Cloudflare

The web app runs on Cloudflare Workers. The API, the job worker, Postgres, and Redis run on a
host you control, reached through a Cloudflare Tunnel. Browsers only ever talk to the site's
origin. To run the backend on Render instead of your own server, follow
`docs/deploy-render.md` in place of steps 1–3 below.

```
browser ──https──> Cloudflare Worker "katzscanner" (apps/web/wrangler.jsonc)
                     ├─ /*      static assets (Vite build), SPA fallback, public/_headers
                     └─ /api/*  worker/proxy.ts: strips /api, adds the Access service token
                                   │
                                   ▼
                     Cloudflare Access (service-token policy) on api.<your-domain>
                                   │  Cloudflare Tunnel, outbound from the host
                                   ▼
host: docker compose --profile app --profile tunnel
      api (FastAPI :8000) · worker · postgres · redis · cloudflared
```

## Why not all of it on Cloudflare

The web app is static, so it belongs on the edge. The rest does not fit Workers:

- The worker clones repositories and runs Semgrep and tree-sitter over them for minutes.
- Dynamic verification starts its own Docker containers and sets up dnsmasq, iptables, and a
  per-run CA inside a network namespace. That needs a Docker daemon and `NET_ADMIN`.
  Cloudflare's container offering runs your image, not a Docker daemon of your own.
- The API needs Postgres and Redis next to the worker.

## One origin, on purpose

The Worker proxies `/api/*` to the API, as the Vite dev server does locally. This has three
consequences:

- The session cookie (`lantern_session`, HttpOnly, Secure, SameSite=Lax) stays first-party.
- There is no CORS to configure.
- GitHub's webhooks come through the same hostname.

Do not set `VITE_API_BASE` to the API's own hostname. That turns the cookie into a
third-party cookie, and browsers block those.

The proxy (`apps/web/worker/proxy.ts`, tested in `worker.test.ts`) does the following:

- **Host.** It takes the upstream host only from `API_ORIGIN`. That must be https; plain http
  is allowed only to localhost. The request path cannot pick the host, so
  `/api//evil.example` stays on the API.
- **Stripped headers.** It drops hop-by-hop headers, and any `X-Forwarded-*` or
  `CF-Access-*` header the client sends.
- **Added headers.** It sets `X-Forwarded-Host`, `X-Forwarded-Proto`, and `X-Forwarded-For`.
  With `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` set, it also adds the Access
  service token. With `API_PROXY_SECRET` set, it sends the API's shared secret in
  `X-Katz-Proxy-Secret`, which a Render-hosted API requires (`docs/deploy-render.md`).
- **Request bodies.** It forwards them byte for byte, so GitHub's webhook signatures still
  verify at the API.
- **Redirects.** It passes redirects to the browser instead of following them, and rewrites
  redirects that point at the API host back through `/api`.
- **Response headers.** It adds `Cache-Control: no-store` unless the API set its own, plus
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- **What it stores.** It logs nothing, stores nothing, and leaves Workers observability off.

Static responses get their headers from `apps/web/public/_headers`:

- a strict CSP: `default-src 'self'`, no inline scripts or styles, `frame-ancestors 'none'`;
- HSTS without `includeSubDomains`;
- `nosniff`, `X-Frame-Options: DENY`, and a restrictive `Permissions-Policy`;
- immutable caching for the fingerprinted `/assets/*` files.

Cloudflare does not apply `_headers` to responses the Worker generates, which is why the
proxy sets its own.

## Steps

### 1. The host

A Linux VM with Docker. As a starting point, 4 vCPUs and 8 GB are enough for the ten-minute
budget on a 20k-line repository; measure your own.

```bash
docker build -f infra/Dockerfile -t lantern:latest .
cp .env.example .env   # then fill it in: the GitHub App settings and the variables below
```

`.env` needs, besides the GitHub App settings in `docs/github-app.md`:

| Variable | Value |
|---|---|
| `LANTERN_WEB_URL` | `https://katz.example.com`, the site's origin. Used for CORS and report links. |
| `LANTERN_COOKIE_SECURE` | `1` (the default). The site is https. |
| `LANTERN_TOKEN_KEY` | A Fernet key (see `docs/github-app.md`) |
| `CLOUDFLARE_TUNNEL_TOKEN` | From step 2 |

The compose file publishes the API on `127.0.0.1:8000` only. Keep it that way, because Docker's
published ports bypass host firewalls such as ufw.

Dynamic verification is off in this setup: the compose worker has no Docker socket.
Mounting `/var/run/docker.sock` into it would give it root on the host. If you want dynamic
verification, run the worker directly on the host, with `LANTERN_JOB_ISOLATION=container`,
under a dedicated user in the `docker` group.

### 2. Cloudflare Tunnel

In Cloudflare Zero Trust, go to **Networks → Tunnels** and create a tunnel. Copy its token
into `CLOUDFLARE_TUNNEL_TOKEN`. Add a public hostname, `api.example.com`, whose service is
`http://api:8000`. Then:

```bash
docker compose --profile app --profile tunnel up -d
```

The host now needs no inbound ports at all.

### 3. Cloudflare Access in front of the API (recommended)

Without Access, `api.example.com` is a public API next to the site. With Access, only the
Worker can reach it.

1. **Zero Trust → Access → Service Auth → Service Tokens**: create a token, for example
   `katzscanner`.
2. **Access → Applications**: add a self-hosted application for `api.example.com` with a
   policy whose action is **Service Auth** and whose rule is that service token.
3. Give the Worker the token:

   ```bash
   cd apps/web
   pnpm exec wrangler secret put CF_ACCESS_CLIENT_ID
   pnpm exec wrangler secret put CF_ACCESS_CLIENT_SECRET
   ```

To add defense in depth, the API can also verify the `Cf-Access-Jwt-Assertion` header against
your team's Access keys. Katz does not do this yet.

### 4. The Worker

For the first deploy, from a machine where you can run `wrangler login`:

```bash
pnpm install
VITE_OPERATOR_CONTACT=katz@example.org VITE_BACKEND_HOST="Example Hosting (Germany)" \
  pnpm --filter @katz/web build:cf
pnpm --filter @katz/web exec wrangler deploy --var API_ORIGIN:https://api.example.com
```

`build:cf` is the public demo build. It shows the demo notice described below; a plain `build`
does not.

`API_ORIGIN` persists across deploys, because `keep_vars` is set in `wrangler.jsonc`. You can
also set it in the dashboard, under **Workers → katzscanner → Settings → Variables**.

The site is now at `https://katzscanner.<your-subdomain>.workers.dev`. For your own domain,
the zone must be on Cloudflare. Either add this to `wrangler.jsonc`:

```jsonc
"routes": [{ "pattern": "katz.example.com", "custom_domain": true }]
```

or add the domain in the dashboard, under **Workers → katzscanner → Settings → Domains &
Routes**.

**Continuous deploys.** `.github/workflows/deploy-web.yml` deploys after `ci` passes on a push
to `main`, and can be run by hand. It stays a no-op until the repository has these:

| Kind | Name | Value |
|---|---|---|
| Secret | `CLOUDFLARE_API_TOKEN` | An API token from the "Edit Cloudflare Workers" template, limited to your account (and zone, for a custom domain) |
| Secret | `CLOUDFLARE_ACCOUNT_ID` | Your account ID |
| Variable | `LANTERN_API_ORIGIN` | `https://api.example.com` (optional once it is set on the Worker) |
| Variable | `LANTERN_OPERATOR_CONTACT` | An email address or URL for questions and deletion requests, shown on the About page |
| Variable | `LANTERN_BACKEND_HOST` | Who hosts the API server, for example `Hetzner Online GmbH (Germany)`, named as a processor |
| Variable | `LANTERN_BACKUP_DAYS` | How many days the host's database backups keep deleted data, for example `7` on Render. Without it, the About page says "for a while" |

It never deploys code from pull requests: it checks that the CI run came from a push to this
repository, not only that the branch was named `main`. Anyone with write access can run it by
hand on any branch.

To roll back: `pnpm exec wrangler rollback`, or pick a version under **Workers → katzscanner →
Deployments**.

### 5. The GitHub App

With a single origin, every GitHub App URL points at the site:

| Setting | Value |
|---|---|
| Homepage URL | `https://katz.example.com` |
| Callback URL | `https://katz.example.com/auth/github/callback` |
| Webhook URL | `https://katz.example.com/api/webhooks/github` |

In `infra/github-app-manifest.json`, this means replacing `https://api.katz.example.com`
with `https://katz.example.com/api`, not with the API's own hostname. Behind Access, the
API's hostname does not accept GitHub's requests.

### 6. Check it

```bash
curl -sI https://katz.example.com/ | grep -i content-security-policy
curl -s https://katz.example.com/api/healthz          # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' https://api.example.com/healthz   # 302 or 403: Access is on
```

Then install the app from the site and run a scan on a small repository.

| Symptom | Cause |
|---|---|
| `503 {"detail": "The API is not configured ... (API_ORIGIN)."}` | `API_ORIGIN` unset, not https, or has credentials in it |
| `502 {"detail": "The API did not respond."}` | Tunnel down, or the `api` container is not running |
| An Access login page, or a 403, under `/api` | The Worker has no service token, or the Access policy does not include it |
| Signed in, but every call returns 401 | The API is reached directly instead of through `/api`, or `LANTERN_COOKIE_SECURE=1` over plain http |
| Webhooks answer 401 `invalid signature` | `GITHUB_WEBHOOK_SECRET` does not match the App's |

## The demo notice

The Cloudflare build (`build:cf`, which reads `apps/web/.env.cloudflare`) tells visitors what
they are using:

- **A banner on every page:** an experimental demo, not a product; Cloudflare processes the
  traffic as a data processor; for real work, host Katz yourself.
- **A "Before you install" box on the Connect page,** before anyone grants the GitHub App
  access to their repositories.
- **An About page (`/about`).** It covers:
  - what is stored, why, and for how long;
  - who else handles the data: Cloudflare, the server's host, and, when
    `LANTERN_DECISION_PROVIDER=jev`, TypeSafe;
  - how to have data deleted;
  - the legal basis and the visitor's GDPR rights.

The page reads the decision provider and the session lifetime from the API's public `/config`,
so it stays true when the configuration changes. The API and worker must use the same `.env`.

Two things are the operator's to fill in:
- **A contact** (`LANTERN_OPERATOR_CONTACT`). Without it, the page tells people to open an
  issue that names only their GitHub login.
- **The server's host** (`LANTERN_BACKEND_HOST`).
- **How long its backups last** (`LANTERN_BACKUP_DAYS`). Deletion removes data from the live
  database only; a managed database's backups keep it until they expire, and the page says so.

GDPR Art. 13(1)(a) requires the controller's identity and contact details. The page names the
maintainer of the repository and uses the contact you set.

Two statements on the page are commitments you have to keep:
- Stored results are kept "until you ask for deletion", and uninstalling the GitHub App does
  not delete them.
- Deletion is manual today: delete the user's runs from the database.

The stated legal basis is legitimate interests (Art. 6(1)(f)). It is your call as controller:
change it in `apps/web/src/pages/AboutPage.tsx` if you rely on something else.

A self-hosted build shows none of this. Every build keeps the footer line saying reports are
not legal advice.

## What this puts Cloudflare in the middle of

TLS ends at Cloudflare's edge, so Cloudflare handles everything the site and API exchange in
the clear. That includes:

- session cookies;
- the GitHub login, name, and avatar of every user;
- findings;
- reports, whose node snippets are redacted but can still show field names and code.

For each deployment, decide these deliberately rather than by default:

- **Processor terms.** Cloudflare acts as your processor, and as a sub-processor for your
  customers' scan data. GDPR Art. 28(3) requires a contract: Cloudflare's Data Processing
  Addendum is incorporated into its self-serve and enterprise terms. Add Cloudflare to your
  sub-processor list, and give customers the notice your own DPA promises under
  Art. 28(2) and (4). Under the CCPA and CPRA, the parallel requirement is a service-provider
  contract (Cal. Civ. Code § 1798.100(d), § 1798.140(ag); 11 CCR § 7051).
- **Transfers.** Edge processing happens wherever the visitor connects, including the US. The
  legal basis is the EU-U.S. Data Privacy Framework adequacy decision (Commission Implementing
  Decision (EU) 2023/1795, GDPR Art. 45) if Cloudflare is certified, and otherwise the SCCs in
  its DPA (Art. 46(2)(c)). Check Cloudflare's current entry on the DPF list rather than
  relying on this document. Keeping processing in the EU (Regional Services, Customer Metadata
  Boundary) is an Enterprise add-on.
- **Your own promises.** If Katz is sold as "your code never leaves our infrastructure",
  a CDN that terminates TLS contradicts that. Putting the host behind the tunnel does not
  change it. The alternative is to serve `apps/web/dist` from the host and point DNS at the
  host directly, without Cloudflare's proxy. You keep the same single-origin layout and give
  up the edge.
- **Logs.** Workers observability is off in `wrangler.jsonc`. If you turn it on, invocation
  logs record request URLs and metadata for every API call, on Cloudflare's retention terms.

Pricing, plan limits, and product names change. Check Cloudflare's current documentation
before relying on the details above. At the time of writing, static asset requests do not
count as Worker invocations, so only `/api` traffic is metered.

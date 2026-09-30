# Setting up the demo from nothing

This guide takes you from no accounts to a working public demo: the site on Cloudflare and the
API, worker, and database on Render. You don't need a terminal. Everything happens in the
Cloudflare, GitHub, and Render dashboards. Dashboard labels change over time, so a button may
be named slightly differently from what is written here.

Do the steps in order, because each step needs something from the one before:

```
Cloudflare ──site address──> GitHub App ──app keys──> Render ──API address + proxy secret──> Cloudflare
```

It costs about $20 a month on Render (`docs/deploy-render.md`). The Cloudflare Workers free
plan, including its builds, and GitHub are free.

Keep a scratch note open while you work. You will collect these values:

| Name | Comes from |
|---|---|
| `SITE` | step 1, for example `https://katzscanner.yourname.workers.dev` |
| `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET` | step 2 |
| `API`, `LANTERN_PROXY_SECRET` | step 3 |

The client secret, private key, webhook secret, and proxy secret are secrets. Keep them in a
password manager, not in the repository or a chat.

## 1. Cloudflare: put the site up

1. Create a Cloudflare account. Open **Workers & Pages**. If Cloudflare has not given you a
   `workers.dev` subdomain yet, choose one. Your site will be
   `https://katzscanner.<subdomain>.workers.dev`. That address is `SITE`.
2. Create the Worker from this repository. In **Workers & Pages**, choose **Create → Import a
   repository**, connect GitHub, pick this repository, and name the Worker `katzscanner`. It
   must be exactly that name, because `apps/web/wrangler.jsonc` names it.
3. The site lives in `apps/web`, not at the top of the repository, so Cloudflare's default
   commands fail. Under **katzscanner → Settings → Build**, set these fields:

   | Field | Value |
   |---|---|
   | Root directory | Leave it empty, or `/` for the repository's top. |
   | Build command | `pnpm --filter @katz/web build:cf` |
   | Deploy command | `pnpm --filter @katz/web exec wrangler deploy` |
   | Non-production branch deploy command (or **Preview command**) | `pnpm --filter @katz/web exec wrangler versions upload` |

   Cloudflare installs the dependencies itself with pnpm, which it detects from the lockfile.
   If that step fails, add the build variable `SKIP_DEPENDENCY_INSTALL` = `1` and put
   `pnpm install --frozen-lockfile && ` in front of the build command.
4. Start a build. Either retry the failed one under **katzscanner → Deployments**, or push to
   `main`. When it finishes, open `SITE`.
   - The Katz home page loads.
   - Sign-in and scans don't work yet: `/api/*` answers 503 until step 4 connects the API.

**Alternative: GitHub Actions.** You can deploy from this repository's `deploy-web` workflow
instead of Cloudflare's builds. It needs these repository secrets:
- `CLOUDFLARE_API_TOKEN`, from **My Profile → API Tokens → Create Token** with the **Edit
  Cloudflare Workers** template;
- `CLOUDFLARE_ACCOUNT_ID`.

Then run **Actions → deploy-web**. Use one route or the other, not both. The workflow does
nothing while those secrets are absent.

## 2. GitHub App: create it by hand

Go to your GitHub avatar → **Settings → Developer settings → GitHub Apps → New GitHub App**.
To make the app under an organization instead, use the organization's settings.

| Field | Value |
|---|---|
| GitHub App name | `Katz DPIA`. It must be unique on GitHub; if it is taken, add a word. |
| Homepage URL | `SITE` |
| Callback URL | `SITE/auth/github/callback` |
| Expire user authorization tokens | Leave it checked. People sign in again after 8 hours, with the site's **Sign in** link. |
| Request user authorization (OAuth) during installation | Check it |
| Setup URL | Leave it empty. GitHub disables it when the box above is checked. |
| Redirect on update | Check it |
| Webhook → Active | Check it |
| Webhook URL | `SITE/api/webhooks/github`. It goes through the site, because the API refuses callers without the proxy secret. |
| Webhook secret | A long random string, from a password manager's generator. This is `GITHUB_WEBHOOK_SECRET`. |
| Repository permissions | Contents: **Read-only**. Metadata: **Read-only** (set automatically). Pull requests: **Read and write**. Checks: **Read and write**. Nothing else. |
| Subscribe to events | **Push** and **Pull request** |
| Where can this GitHub App be installed? | **Any account**, so visitors can install it |

Create the app. On its settings page, collect:

- **App ID** → `GITHUB_APP_ID`.
- **Client ID** → `GITHUB_CLIENT_ID`.
- **Generate a new client secret** → `GITHUB_CLIENT_SECRET`. GitHub shows it once.
- **Generate a private key**. This downloads a `.pem` file, and its whole contents are
  `GITHUB_APP_PRIVATE_KEY`.
- The app's public link is `https://github.com/apps/<slug>`. That last part is
  `GITHUB_APP_SLUG`.

`docs/github-app.md` explains each permission and flow.

## 3. Render: the API, worker, and database

1. Create a Render account and add a payment method. The Blueprint uses paid plans.
2. Go to **New → Blueprint**, connect GitHub, and pick this repository. Render reads
   `render.yaml` and lists `katz-api`, `katz-worker`, `katz-queue`, and `katz-db`.
3. Render asks once for the values it cannot generate:
   - `LANTERN_WEB_URL`: `SITE`.
   - `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`,
     `GITHUB_WEBHOOK_SECRET`: from step 2.
   - `GITHUB_APP_PRIVATE_KEY`: paste the `.pem` file's whole contents. If the field keeps only
     one line, replace each line break with `\n`. Both forms work.
4. Apply. The first build takes several minutes. Then collect:
   - **`API`**: the `katz-api` service's address, shown at the top of its page. It is
     `https://katz-api.onrender.com`, or that with a suffix if the name was taken.
   - **`LANTERN_PROXY_SECRET`**: under **Environment Groups → katz-shared**. Reveal it and
     copy it.
5. Check the API. `API/healthz` answers `{"status":"ok"}`, and `API/config` answers 404. The
   404 is the proxy secret working: only the site may call the API.

## 4. Connect the site to the API

1. In Cloudflare, go to **Workers & Pages → katzscanner → Settings → Variables and Secrets**
   and add two entries:
   - type **Secret**, named `API_PROXY_SECRET`, with the value of `LANTERN_PROXY_SECRET`;
   - type **Text**, named `API_ORIGIN`, with the value `API`.

   These are used while the site runs, and deploys keep them.
2. Under **katzscanner → Settings → Build → Build variables and secrets**, add the demo
   notice's facts. They are built into the page, so they belong to the build, not the running
   site:
   - `VITE_BACKEND_HOST`: `Render Services, Inc. (US company; servers in Frankfurt, Germany)`.
   - `VITE_BACKUP_DAYS`: `7`.
   - `VITE_OPERATOR_CONTACT`: an email address or URL you are happy to publish. It is where
     people ask for deletion.
3. Build again: retry the latest build under **Deployments**, or push to `main`.

On the GitHub Actions route, the same values go in this repository's **Settings → Secrets and
variables → Actions → Variables** instead: `LANTERN_API_ORIGIN`, `LANTERN_BACKEND_HOST`,
`LANTERN_BACKUP_DAYS`, `LANTERN_OPERATOR_CONTACT`. Then run **deploy-web** again.

## 5. Check it end to end

- `SITE/api/healthz` answers `{"status":"ok"}` through Cloudflare.
- The banner says **Public repositories only** and **Scans are deleted after 3 days**. The
  deletion promise appears only while deletion runs; the API runs it when it starts and
  every hour after that.
- The About page names Cloudflare and Render, the backup window, and your contact.
- Install the app on a public repository, start a scan, and open the report.
- Open `SITE` in a private window and use **Sign in**. You land on your repositories without
  installing again.

If a step fails, see the troubleshooting tables in `docs/deploy-render.md` and
`docs/deploy-cloudflare.md`.

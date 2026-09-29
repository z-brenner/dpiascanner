# GitHub App setup

Lantern reads repositories through a GitHub App. The app clones code at a commit, posts a
check run and one comment on pull requests, and receives webhooks for installations,
pushes, and pull requests.

## Permissions and events

| Permission | Access | Why |
|---|---|---|
| Contents | read | Shallow clone of the commit being scanned; list pull request files |
| Metadata | read | Required by GitHub for every app; repository names and default branches |
| Pull requests | write | Create and update the single Lantern comment on a pull request |
| Checks | write | Report run status and a findings summary as a check run |

Events: `push` (full run on the default branch) and `pull_request` (incremental run on
`opened`, `reopened`, and `synchronize`). GitHub delivers `installation` and
`installation_repositories` events to every GitHub App automatically, so they are not listed
in the manifest's `default_events`; Lantern handles `installation` to keep its record of
installations current.

Lantern asks for nothing else: no write access to code, no issues, no members, no secrets.

## Create the app

1. Edit `infra/github-app-manifest.json`. Replace `lantern.example.com` with the web app's
   origin and `api.lantern.example.com` with the API's origin. On Cloudflare
   (`docs/deploy-cloudflare.md`) the API is served under the site at `/api`, so the webhook
   URL is `https://lantern.example.com/api/webhooks/github`. Set `public` to `true` only if
   other organizations should be able to install it.
2. Register it through the manifest flow. Serve this form from any page and submit it
   (for an organization, post to
   `https://github.com/organizations/<org>/settings/apps/new` instead):

   ```html
   <form action="https://github.com/settings/apps/new?state=<random>" method="post">
     <input type="hidden" name="manifest" value='<contents of github-app-manifest.json>'>
     <button>Create Lantern DPIA app</button>
   </form>
   ```

3. GitHub redirects to `redirect_url?code=<code>&state=<random>`. Check `state`, then
   exchange the code within an hour:

   ```bash
   curl -X POST -H "Accept: application/vnd.github+json" \
     https://api.github.com/app-manifests/<code>/conversions
   ```

   The response carries `id`, `slug`, `client_id`, `client_secret`, `webhook_secret`, and
   `pem`. Store them in your secret manager; GitHub shows `pem` and `client_secret` once.

## Configure Lantern

| Variable | Value |
|---|---|
| `GITHUB_APP_ID` | `id` from the conversion |
| `GITHUB_APP_PRIVATE_KEY` | `pem` (literal `\n` sequences are accepted), or `GITHUB_APP_PRIVATE_KEY_PATH` |
| `GITHUB_WEBHOOK_SECRET` | `webhook_secret`. Without it every webhook is rejected. |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | For the user authorization during installation |
| `GITHUB_APP_SLUG` | `slug`, used for "install the app" links |
| `LANTERN_TOKEN_KEY` | A Fernet key; user and installation tokens are encrypted with it at rest. Generate with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |
| `LANTERN_WEB_URL` | The web app's origin (CORS and links in PR comments) |
| `DATABASE_URL`, `REDIS_URL` | Postgres (`postgresql+psycopg://...`) and Redis |

Rotating `LANTERN_TOKEN_KEY` invalidates stored tokens: users sign in again, and
installation tokens are minted again on demand.

## How the flows work

- **Installation.** Because the manifest sets `request_oauth_on_install`, GitHub sends the
  user to the callback URL with `code`, `installation_id`, and `setup_action`. The web app
  posts them to `POST /auth/github/callback`. The API exchanges the code for a
  user-to-server token, records the user's installations, and sets an HttpOnly session
  cookie (stored server side by hash).
- **Repositories.** `GET /repos` lists what the user can reach through the app's
  installations. `POST /repos/resolve` accepts `https://github.com/o/r`, `github.com/o/r`,
  `o/r`, and `git@github.com:o/r.git`, and says whether the repository is installed, public
  (scannable without the app), or inaccessible, with an install link.
- **Clones.** The worker mints an installation token (JWT signed with the app key, token
  cached until five minutes before expiry, encrypted in the database) and fetches the one
  commit at depth 1. The token reaches git through `GIT_CONFIG_*` environment variables as
  an HTTP header, never in a URL or command line. The clone is deleted after the run.
- **Pull requests.** On `opened` or `synchronize`, the API opens a queued check run and
  enqueues an incremental run against the latest completed run on the base branch. The
  worker completes the check (`success`, or `neutral` when new findings are high or
  critical; Lantern does not block merges) and creates or updates one comment carrying a
  hidden marker, so later pushes edit it instead of adding comments.

## Local development

Expose the API to GitHub with a webhook relay such as smee.io:

```bash
npx smee-client --url https://smee.io/<channel> --target http://localhost:8000/webhooks/github
```

and set the app's webhook URL to the smee channel while developing.

# fixtures

Synthetic repositories with planted personal-data flows ("canaries") and manifests of the
findings Lantern must produce. The analyzer is written against these, never the other way
around: when a real repository exposes a gap, add the pattern here first.

| Fixture | Stack | Purpose |
|---|---|---|
| `clean-python` | FastAPI, SQLAlchemy | False-positive control. No personal data, plus deliberate lexicon traps. Must yield zero paths and zero findings. |
| `canary-python` | FastAPI, SQLAlchemy, Mixpanel, Sentry, Stripe, boto3 | Twelve planted flows, C01 to C12. |
| `canary-typescript` | Express 5, Prisma 7, Mixpanel, Sentry, Stripe, AWS SDK v3 | The same twelve flows, reimplemented. |
| `mock-server` | Python stdlib | Records every request it receives to JSONL. |

## Manifests

Each fixture has a `MANIFEST.yaml`. It is test data: it must never be passed to the
analyzer, the decision provider, or the LLM baseline. Fixture source code must not contain
comments that point at a planted flow.

Location conventions:

- **Source line.** Python: the entry-point parameter that receives the value (a FastAPI body
  model, `Form`, or query parameter), or the ORM read call. TypeScript: the first expression
  that reads the field from the request (`req.body` destructuring or `req.body.<field>`), or
  the ORM read call.
- **Sink line.** The line of the call that hands the data to its destination.
- **Snippet.** Text that must appear on that line. `packages/analysis/tests/test_fixtures.py`
  enforces it, so a manifest cannot silently drift from the code.

`expected_findings` lists finding categories that must be attached to a flow;
`forbidden_findings` lists categories that must not be. Extra findings on a flow are allowed
only if they are true (for example, an S3 write with no lifecycle policy is also an
indefinite-retention finding).

## Running a fixture

Every fixture runs on its own:

```bash
cd fixtures/canary-python
pip install -r requirements-dev.txt
pytest                       # minimal test suite, third parties stubbed in-process
python scripts/exercise.py   # drives every reachable flow with canary values
```

```bash
cd fixtures/canary-typescript
npm ci && npx prisma generate
npm test
npm run exercise
```

`lantern.yml` in each fixture lists the environment variables that hold third-party
endpoints. Dynamic verification rewrites them to point at the mock server:

- `rewrite: prefix` (default): `https://api.stripe.com` becomes
  `http://127.0.0.1:PORT/_host/api.stripe.com`, and the mock server records host
  `api.stripe.com`.
- `rewrite: port`: for SDKs that accept only host, port, and protocol (stripe-node), the
  mock server binds a dedicated port for that vendor host.

Canary values are fixed in `canaries.yaml` so they can be matched in outbound traffic,
including common encodings (URL encoding, base64, hashes).

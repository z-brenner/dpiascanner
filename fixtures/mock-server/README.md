# mock-server

A standard-library HTTP server that records every request it receives to a JSONL file and
returns plausible minimal responses for Stripe, S3, Sentry, Mixpanel, and generic JSON APIs.

```bash
python mock_server.py --port 8099 --out requests.jsonl
```

Each JSONL record has `ts`, `method`, `host`, `path`, `query`, `headers` (authorization and
cookies redacted), `body_b64` (decompressed if gzip or deflate), and `body_sha256`.

The vendor host is recovered from a `/_host/<host>/` path prefix, from a dedicated per-vendor
port, or from the Host header. See `fixtures/README.md`. In tests, use `MockServer` as a
context manager.

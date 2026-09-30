# lantern_worker.dynamic

Dynamic verification: run a repository with synthetic canary values, record every
outbound request, and reconcile what was sent with the static graph's sink nodes.

**Inputs:** a repository checkout and its `DataFlowGraph` (every sink node carries
`attrs["endpoints"]` from `lantern_analysis.endpoints`).
**Outputs:** the same graph, with `attrs["dynamic"]` on every sink node, new dynamic-only
sink nodes for unexpected destinations, and `summary["dynamic"]`: the run's steps, status
counts, and the reduced request log. `run_pipeline` runs it between graph construction and
classification when `PipelineConfig.dynamic` is set.

```python
from lantern_worker.dynamic import DynamicVerifier, Limits
from lantern_worker.dynamic.sandbox import DockerSandbox

report = DynamicVerifier(DockerSandbox.from_env(), Limits()).verify(repo_path, graph)
```

## What runs

`plan.py` picks the modes, in the order Prompt 6 specifies:

1. the repository's **tests** (pytest; `npm test` unless it is npm's placeholder);
2. its **declared run script**, `dynamic.run` in `lantern.yml`;
3. otherwise **route exercise**: start the app (`dynamic.start`, a detected FastAPI,
   Starlette, Flask, Quart, or Litestar app object, or `npm start`) and drive it with
   `routes.py`.

Modes run in that order, and the verifier stops at the first one after which every
reachable network sink is verified. Tests seldom carry canary values, so they usually
reveal hosts without verifying them; that is why a later mode can still run. This is a
superset of "run the tests if present, else ...": a repository whose tests verify
everything never reaches the later modes.

`routes.py` prefers the application's own OpenAPI document (complete paths and body
schemas); otherwise it uses the routes and request fields from the static graph, and
retries a 404 with a router prefix guessed from the file name (`routes/users.py` →
`/users`). Values come from `canaries.yaml`, chosen by field name, then by schema format;
other fields get plausible filler (enum values, `1` for ids).

## What is recorded

`observed.py` reduces each raw request to host, method, path (secrets redacted), scheme,
headers (only a safe list keeps values), query keys, body SHA-256, body size, body field
names, and canary hits. The raw capture, which holds bodies, is deleted as soon as it is
reduced. Query values and bodies are never stored.

`canaries.py` finds canary values as sent, URL-encoded, base64 (including base64 tokens
inside query strings, as mixpanel-node sends them), gzip or deflate, JSON-escaped,
case-folded, digits-only (phones), and as MD5, SHA-1, or SHA-256 of the normalized value.

## Reconciliation

| status | reason | meaning |
|---|---|---|
| `verified` | `canary-observed` | a request to one of the sink's hosts carried a canary |
| `verified` | `canary-in-process-output` | log sink: a canary whose kind matches a field flowing into it appeared in stdout or stderr |
| `inferred` | `observed-without-canary` | the host was contacted, but no canary was in the request |
| `inferred` | `not-exercised` | no request reached the host |
| `inferred` | `unreachable` | static analysis found no entry point that reaches the sink |
| `inferred` | `no-known-host` | static analysis could not name the destination |
| `inferred` | `not-network-observable` | first-party store or file write |
| `inferred` | `not-observed-in-output` | log sink whose data never appeared in the output |
| `observed-unexpected` | `host-not-in-static-graph` | a new sink node, `file: "<dynamic>"`, `evidence: dynamic-only` |

Host patterns may have a leading `*.`. Attribution is by host, not call site: two Sentry
calls that share `*.ingest.sentry.io` are both verified by one canary-bearing event, and
each lists the other in `shared_host_with`. Log-output verification is by data kind, which
is weaker evidence; the reason string keeps it distinct. Dynamic-only nodes become
`observed_unexpected_destination` findings; every other finding anchored on a sink carries
the sink's status in `evidence.verification`.

SMTP (`email` sinks) is only half covered. The recorder speaks HTTP and TLS, so an SMTP
payload is never read and cannot verify a sink, and a client waiting for the server's
greeting stalls until its step times out. Static analysis does not resolve the SMTP host yet,
so an `email` sink reads `no-known-host`, and the relay's name, which the app does resolve,
shows up as an `observed-unexpected` destination with method `DNS`.

## Backends

**`DockerSandbox`** (`sandbox.py`) is the only place a scanned repository's code runs.

- A throwaway CA per run (`generate_ca`, `pathlen=0`, valid two days). The certificate is
  baked into the app image and appended to every CA bundle on the Python path
  (`images/inject_ca.py`: certifi, stripe-python's own bundle) and the system bundle;
  Node gets `NODE_EXTRA_CA_CERTS`. The key goes only into the proxy container.
- The proxy container owns the network namespace: no network (`--network none` plus a dummy
  default route) or, where the kernel lacks dummy interfaces, an `--internal` Docker network
  with no gateway. dnsmasq resolves every name to 198.51.100.1 and logs the query, an
  iptables NAT rule sends all TCP to mitmproxy in transparent mode, and everything else
  that leaves the namespace is dropped, including UDP and Docker's embedded resolver. The
  `proxy/recorder.py` addon records each request and answers it itself; nothing is
  forwarded. Names that were resolved but never sent HTTP (SMTP, database hosts) appear as
  `DNS` records.
- The app container joins that namespace with every capability dropped,
  `no-new-privileges`, a read-only root filesystem, a size-capped `/tmp` tmpfs, and
  `--cpus`, `--memory` (no swap), `--pids-limit`, and `fsize`/`cpu` ulimits (`Limits`).
  Each step has a timeout inside the run's wall-clock budget.
- Route exercise runs `routes.py` in a helper container from the proxy image, in the same
  namespace, against the app on loopback.
- Containers, the app image (which contains the repository), and the network are removed
  after every run. Proxy images are tagged by content hash and hold no repository data.

Dependency installation happens in `docker build`, which needs registry access, so install
scripts run with network access inside the build container. The build context holds the
repository and Katz's CA certificate, nothing else. Behind a TLS-intercepting proxy,
set `LANTERN_BUILD_NETWORK=host`, `LANTERN_BUILD_CA_CERT`, and `LANTERN_BUILD_HTTPS_PROXY`.

**`LocalFixtureBackend`** (`local.py`) runs Katz's own fixtures on the host against
`fixtures/mock-server`, rewriting `lantern.yml`'s `dynamic.endpoints` to the mock server. It
refuses any path outside `fixtures/`. It exists so the verifier can be tested without Docker.

## Tests

- `make test-dynamic`: the canary fixtures under the local backend. Every reachable
  third-party sink in both manifests must be verified; C11 (dead code) stays `inferred`.
- `make test-docker`: the same fixtures in the Docker sandbox, plus an egress probe (names
  resolve to the sink address, UDP and Docker DNS are blocked, every TLS peer is the proxy).

## Known limits

- Canaries prove that data reached a host, not which call sent it.
- Repositories whose app needs a database or other service to start get only the modes
  that work without it. Service containers are not provided yet.
- The read-only root filesystem breaks apps that write inside their source tree.
- Non-HTTP protocols are recorded by destination name only (DNS log), with no content.

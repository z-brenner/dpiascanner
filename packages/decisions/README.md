# lantern-decisions

The decision layer. It answers fixed, typed, versioned questions about graph targets (nodes
and edges) with calibrated probabilities. It never generates free text.

## Inputs and outputs

**Input:** a batch of `DecisionRequest`s. Each carries a `target_id`, a `target_type`
(`source`, `edge`, `sink`, `retention`), a state payload (JSON, schema `lantern.state/v1`,
see `state.py`), and a question set id plus version.

**Output:** one `DecisionResult` per question: `answer`, `probability` of that answer, the
full `distribution` over options, `provider`, `provider_version`, `state_hash`, and `raw`
provider output (kept so every finding can be reproduced and diffed).

States must be redacted before they reach this package. The worker builds them; this
package reads them.

## Question sets

Structure is in `question_sets.py` (frozen dataclasses). Content, meaning the descriptions
the model reads, is in `question_sets/v1/*.yaml`:

| Set | Applies to | Questions |
|---|---|---|
| `source_v1` | source nodes | `data_category` (choice), `special_category_art9` (yes/no), `relates_to_minor` (yes/no), `identifiability` (score 1-5) |
| `edge_v1` | edges | `transformation` (choice), `residual_identifiability` (score 1-5) |
| `sink_v1` | sink nodes | `destination_class` (choice), `sale_or_share_cpra` (yes/no), `purpose` (choice) |
| `retention_v1` | retention points | `has_expiry` (yes/no), `plausible_retention` (choice) |

Released versions are immutable. `question_sets/v1/LOCK.yaml` pins a SHA-256 fingerprint
of each set, and `load_bundle("v1")` refuses to load if the YAML no longer matches. To change
wording or options, copy the directory to `v2`, edit, and generate a new lock. Do not
regenerate the v1 lock to hide an edit.

## Providers

All providers implement `DecisionProvider._decide_batch` for one chunk. The base class
chunks arbitrarily large inputs, preserves ordering, and validates every result against the
contract (complete distribution over the question's options, sums to 1, `probability`
equals `distribution[answer]`). `make_provider("stub" | "jev")` builds one by name, or from
`LANTERN_DECISION_PROVIDER`.

- **`StubProvider`**: deterministic and offline. Answers from exact state-hash pins, then
  ordered rules in `stub_rules/v1.yaml` keyed on analyzer evidence (lexicon hints, rule ids,
  registry entries, transform hints, retention evidence), then deliberately low-confidence
  defaults, then a uniform distribution. Distributions are shaped to look like model output.
  The rules are tuned to the fixtures, so tests that use it prove plumbing, not quality.
- **`JevProvider`**: the hosted TypeSafe AI Jev model. Reads `TYPESAFE_API_KEY` and
  `TYPESAFE_BASE_URL` (plus optional `TYPESAFE_RPS`, `TYPESAFE_BATCH_SIZE`,
  `TYPESAFE_TIMEOUT_S`). It provides:
  - retries with exponential backoff and jitter, honoring `Retry-After`;
  - a token-bucket rate limiter;
  - a cache keyed on provider, state hash, and question set version, so re-runs on unchanged
    code make zero API calls (`SQLiteDecisionCache` by default);
  - request and response logging with each state replaced by its hash;
  - an optional `payload_guard` (the worker injects its secrets scanner) that can block a
    state before it leaves the process.

  **Every assumption about the Jev wire format is in `jev/adapter.py`**, marked
  `TODO(jev-api)`. The API is in early access and the format has not been confirmed.

Determinism for a hosted model comes from the cache: the first answer for a given state and
question set version is pinned, so the same input and question set version produce
identical outputs.

## Calibration

```bash
uv run python -m lantern_decisions.calibration --provider stub --out calibration-report/
uv run python -m lantern_decisions.calibration --provider jev  --out calibration-report/
```

Outputs `calibration.json`, `calibration.md` (accuracy, expected calibration error,
per-question accuracy, ECE, MAE for scores, confusion matrices, misses), and
`reliability.png`.

The shipped dataset, `calibration/data/fixtures_v1.jsonl`, is generated from the fixture
manifests by `python -m lantern_decisions.calibration.from_manifests`, and a test fails if it
goes stale. Its states contain raw evidence only (snippet, field name, language) and no
analyzer hints, so labels cannot leak into inputs. The stub, which reads only hints, scores
poorly on it by design (accuracy about 0.66, ECE about 0.16). Run the harness against Jev
before trusting it on anything but the fixtures. Around 90 examples is enough to catch a
badly miscalibrated model. It is not enough to certify a good one.

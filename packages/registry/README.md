# lantern-registry

A curated registry of third-party SDKs and what they do with data. **Entries are evidence,
not verdicts.** The analyzer uses them to turn SDK calls into sink nodes with known
semantics and to detect vendor mitigation hooks; the decision layer still classifies what
the caller actually passed.

**Input:** import specifiers and package names from a repository.
**Output:** `RegistryEntry` objects (vendor, destination class, auto-collected data,
endpoints and regions, processor and service-provider claims, DPA link, mitigation hooks,
review date), and for packages not in the registry, `DependencyProfile`s inferred from the
package's own source.

## registry.yaml

One entry per SDK family, covering Sentry, Datadog, New Relic, Segment, Mixpanel,
Amplitude, PostHog, Google Analytics and gtag, Firebase and Firebase Analytics, the Meta
Pixel and Conversions API, Stripe, Twilio, SendGrid, Mailchimp, Intercom, HubSpot, Braze,
OneSignal, OpenAI, Anthropic, Google Gemini, AWS (Python and JS), Google Cloud, and Azure.

Fields the analyzer uses:

- `imports`: module paths (Python, matched on the full dotted path) and npm specifiers
  (globs allowed).
- `sink_calls` and `setup_calls`: which methods send data (`"*"` for all but setup).
- `event_paths`: where a sink call lands in the vendor's event, so a scrubber hook
  (`before_send` removing `user.email`) can be matched to the flows it actually covers.
- `mitigation_hooks`: `event_scrubber` hooks are resolved and read statically; `flag`
  hooks record their literal value.

Fields the report uses: `vendor`, `destination_class`, `auto_collected`, `endpoints`,
`processor_claims`, `dpa_url`, `last_reviewed`, `reviewed_by`.

**Status:** every entry is an initial draft (`reviewed_by: initial draft, not legal review`).
Contested roles are recorded as contested, not smoothed over:

- Meta Pixel: joint controller under *Fashion ID* (C-40/17), and not a CPRA service
  provider outside Limited Data Use.
- Google Analytics: CPRA status depends on account settings.
- Gemini API: depends on whether the paid or unpaid tier is used.

A human must confirm each claim against the linked terms before a DPIA relies on it.

## Validation

```bash
uv run python -m lantern_registry.validate               # structure (runs in CI tests)
uv run python -m lantern_registry.validate --check-urls  # plus URL reachability (monthly workflow)
```

A 401, 403 or 429 is reported as "blocked" and needs a manual check; it usually means bot
protection. Any other non-2xx or 3xx status fails the run.

## Unregistered dependencies

`resolver.resolve_unregistered` handles imported packages that no entry covers. It:

1. finds each package's pinned version in a lockfile (requirements, uv.lock, poetry.lock,
   Pipfile.lock, package-lock.json, pnpm-lock.yaml, yarn.lock);
2. fetches the package's source (PyPI or npm, or a local mirror for offline runs) with
   safe extraction;
3. runs the injected analyzer one level deep to find which public functions reach a
   network call and which hosts appear in its code.

Results are capped per run (default 10), skipped with a reason when no version is pinned,
and marked `inferred-from-dependency-source`. The analysis package turns calls into those
functions into `dependency` sinks. Enable it with `AnalysisOptions(dependency_depth=1)`.

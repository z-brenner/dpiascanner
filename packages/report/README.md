# lantern-report

Turns a data-flow graph plus decisions into findings, diffs findings between runs, and (in
Prompt 7) renders the DPIA.

**Inputs:** a `DataFlowGraph` (lantern-analysis) and `DecisionResult`s (lantern-decisions).
**Outputs:** a `FindingsResult`, meaning findings plus low-confidence decisions that no
finding already reports, and a `FindingsDiff` between two runs.

## Findings (`findings.py`)

A finding is anchored on a graph node: the sink for most categories, the source for
special-category data, the retention node for retention. It lists the nodes, edges, flows,
and decisions behind it, plus the commit, question set version, and provider, so it can be
reproduced.

| Category | Condition |
|---|---|
| `personal_data_to_third_party` | Reachable, resolved path; source `data_category` is not `none`; sink `destination_class` is analytics, ad_tech, unknown_third_party, ai_model_provider, communications, or cross_border; lowest identifiability along the path is 3 or more. |
| `special_category_processing` | Source `special_category_art9 = yes`. |
| `reversible_obfuscation` | An edge classified `reversible_encoding` on a path into a log or third party. |
| `indefinite_retention` | Retention node with `has_expiry = no` and `plausible_retention` of years or indefinite. |
| `logging_of_personal_data` | Sink class `log`, with identifiability of 3 or more along the path. |
| `cross_border_ambiguity` | Sink with conflicting region configuration (from the graph). |
| `sale_or_share_candidate` | Sink `sale_or_share_cpra = yes`. |
| `unresolved_flow` | A path containing an unresolved step. It gets no other category. |
| `unreachable_flow` | A path not reachable from any entry point. Always informational, and it gets no other category. |
| `mitigated_partially` | A registry SDK with a scrubber hook that covers some flows while others leak. |
| `personal_data_processing` | Added beyond the prompt: a reachable personal-data flow that no other category covers (for example Stripe, or a first-party write with a retention policy). Without it, those flows would be absent from the DPIA's description of processing. |
| `observed_unexpected_destination` | Added with dynamic verification: the sandbox saw a request to a host that no static sink names. Evidence is the host, methods, paths, body field names, and canaries observed. Base medium; one level lower when no canary was in the requests (`no_personal_data_observed`, severity table v2). |

**Confidence.** A finding whose supporting decisions include one below the threshold
(default 0.75) is `unresolved`. Its `unresolved_reasons` list each such decision with its
full distribution. Low-confidence decisions on reachable flows that support no finding
appear in `unresolved_decisions`, so nothing uncertain is silently dropped.

## Severity (`severity.yaml`)

Each category has a base level. Modifiers each move it one level: special category (+1);
a minor (+1); sensitive data such as government id, health, biometric, financial, precise
location, or credentials (+1, not counted again after special category); a risky
destination such as ad tech, unknown third party, AI provider, or cross-border (+1); and
low identifiability of 2 or less (−1). Unreachable findings are always informational. The
modifiers that applied are stored on the finding.

## Statutes (`statutes.yaml`)

The map goes from category, data category, and destination to GDPR articles and recitals,
Cal. Civ. Code sections, CPPA regulations, and other state statutes (Virginia, Colorado,
Connecticut, Washington MHMDA, Illinois BIPA, COPPA). Entries marked `verify: true` need a
check of numbering or effective date. `register_state_law_provider` plugs in a
jurisdiction lookup.

## Diffing (`diff.py`)

Findings are matched on a stable key: category, anchor symbol path (anonymous-function
positions removed), rule ids, and entry route. Line numbers are not part of the key.
Buckets: new, resolved, changed severity, changed classification, and changed status.

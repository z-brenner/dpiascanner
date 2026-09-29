# lantern-report

Turns a data-flow graph plus decisions into findings, diffs findings between runs, and
renders the DPIA as JSON, Markdown, self-contained HTML, and DOCX.

**Inputs:** a `DataFlowGraph` (lantern-analysis) and `DecisionResult`s (lantern-decisions).
**Outputs:** a `FindingsResult`, meaning findings plus low-confidence decisions that no
finding already reports, a `FindingsDiff` between two runs, and a `Report`.

```python
report = build_report(
    graph, findings, decisions, RunInfo("acme/app", sha, repo_url="https://github.com/acme/app")
)
render_markdown(report)
render_html(report)
render_json(report)
render_docx(report)  # bytes
```

or from a stored run: `python -m lantern_report RUN_DIR --format all --out reports/ --repo-url ...`.

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

## DPIA (`dpia.py`)

Sections follow GDPR Art. 35(7) and the ICO DPIA template:

1. Need for a DPIA: screening criteria present in the code, each cited.
2. Description of the processing (Art. 35(7)(a)): nature, scope, context, purposes, from
   findings and graph statistics.
3. Consultation: left for the controller.
4. Necessity and proportionality (Art. 35(7)(b)), with the ICO step 4 questions the code
   cannot answer, each linked to the findings that raise it.
5. Risk register (Art. 35(7)(c)): likelihood is remote (unreachable), possible (static
   evidence only), or probable (observed by dynamic verification); severity of harm maps
   Katz severity; overall risk is likelihood times severity on a 3x3 scale. This scale is
   Katz's, and the report says so.
6. Measures (Art. 35(7)(d)): an option per finding; effect, residual risk, and approval are
   blank for the controller.
7. Sign-off: the ICO rows with names left blank.
8. California (CCPA as amended by the CPRA): categories of personal information by
   § 1798.140(v)(1) letter, sources, purposes, third parties (with the vendor's own
   service-provider claim from the registry, labeled as such), sale or share determinations,
   sensitive personal information (§ 1798.140(ae)), and retention (§ 1798.100(a)(3)).
9. Unresolved flows and decisions: each unresolved finding with its unresolved steps, the
   low-confidence decisions on it, dynamic evidence if any, and specific instructions ("Determine
   the runtime value of `constants.PARTNER_CONTACT_FIELD` and whether the lookup at
   app/partners.py:10 selects phone").
10. Coverage: resolved path fraction, dependencies analyzed and skipped, whether dynamic
    verification ran, and every sink's verified or inferred status.

Appendix A lists every finding with its nodes and edges (file and line links at the commit
SHA) and decisions with probabilities. Appendix B lists the statutes cited.

Only sinks that hand data to another party count as third parties (a registry SDK, a
dependency's service, an HTTP call, or a dynamic-only destination). The application's own
logs and database are not disclosures.

## Narrative (`narrative.py`)

Every sentence in a narrative section is generated from findings and cites them as
`[F-0042]`. `ReportContext.brief` is the only view of a finding a drafter gets: categories,
field names, entry routes, destination names, decisions, verification status, and statute
citations. It carries no source code, snippets, evidence text, or file paths.

- `TemplateDrafter`: fixed sentence templates per section. No model.
- `LLMDrafter` (optional; `AnthropicClient` reads `ANTHROPIC_API_KEY` and
  `LANTERN_NARRATIVE_MODEL`): returns `{"sentences": [...]}`. `validate_sentences` rejects a
  sentence with no citation, a malformed citation, or a citation of a finding that was not in
  the section's input. A rejected draft is regenerated with the rejections as feedback;
  after two failed attempts the section uses the template drafter, and the narrative records
  the attempts and rejections.

## Renderers (`render.py`, `docx_render.py`)

JSON includes everything (sections, findings with nodes, edges, and decisions, all
decisions, statutes, the graph summary). Markdown and HTML link citations to the finding;
HTML is one file with inline CSS, collapsible findings, and GitHub links at the SHA. DOCX
uses bookmarks on finding headings and internal hyperlinks for citations.

"""Assemble the DPIA from a run's graph, findings, and decisions.

Structure follows GDPR Art. 35(7) and the ICO DPIA template (steps 1 to 7), then a CCPA/CPRA
section, the unresolved flows, coverage, and an appendix of every finding with the nodes,
edges, and decisions behind it. Narrative prose is drafted per section from code-free
finding briefs (``narrative.py``); tables are generated directly. Anything the code cannot
tell (lawful basis, consultation, sign-off) is left for the controller and says so.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from lantern_analysis.model import DataFlowGraph
from lantern_decisions.question_sets import load_bundle
from lantern_report.context import ReportContext, RunInfo, destination_name
from lantern_report.findings import Finding, FindingsResult, UnresolvedReason
from lantern_report.narrative import (
    CATEGORY_LABELS,
    PURPOSE_LABELS,
    SENSITIVE_PI,
    LLMDrafter,
    Narrative,
    SectionBrief,
    TemplateDrafter,
    draft_section,
    measure_for,
    split_sentences,
)

REPORT_SCHEMA = "lantern.report/v1"
CCPA_CATEGORIES = {
    "identifier": ["(A) Identifiers"],
    "contact": ["(A) Identifiers", "(B) Personal information in customer records (§ 1798.80(e))"],
    "government_id": ["(A) Identifiers"],
    "financial": ["(B) Personal information in customer records (§ 1798.80(e))"],
    "health": ["(B) Personal information in customer records (§ 1798.80(e))"],
    "demographic": ["(C) Characteristics of protected classifications"],
    "biometric": ["(E) Biometric information"],
    "behavioral": ["(F) Internet or other electronic network activity information"],
    "precise_location": ["(G) Geolocation data"],
    "coarse_location": ["(G) Geolocation data"],
    "credentials": ["(A) Identifiers"],
}
SEVERITY_TO_HARM = {
    "informational": "minimal",
    "low": "minimal",
    "medium": "significant",
    "high": "severe",
    "critical": "severe",
}
_LEVEL = {"remote": 1, "possible": 2, "probable": 3, "minimal": 1, "significant": 2, "severe": 3}
SIGNOFF_ROWS = [
    "Measures approved by",
    "Residual risks approved by",
    "DPO advice provided",
    "Summary of DPO advice",
    "DPO advice accepted or overruled by",
    "Consultation responses reviewed by",
    "This DPIA will be kept under review by",
]
CONTROLLER_QUESTIONS = [
    (
        "What is the lawful basis for each processing purpose?",
        ("personal_data_processing", "personal_data_to_third_party", "special_category_processing"),
    ),
    (
        "Does the processing achieve its purpose, and is there a less intrusive way to do so?",
        ("personal_data_to_third_party", "logging_of_personal_data"),
    ),
    (
        "How is function creep prevented?",
        ("personal_data_to_third_party", "sale_or_share_candidate"),
    ),
    (
        "How are data quality and data minimisation ensured?",
        ("logging_of_personal_data", "personal_data_to_third_party", "mitigated_partially"),
    ),
    (
        "What information is given to individuals, including recipients and retention periods?",
        ("personal_data_to_third_party", "indefinite_retention", "observed_unexpected_destination"),
    ),
    (
        "How are individuals' rights supported?",
        ("indefinite_retention", "special_category_processing"),
    ),
    (
        "What measures ensure processors comply?",
        ("personal_data_to_third_party", "mitigated_partially"),
    ),
    ("How are international transfers safeguarded?", ("cross_border_ambiguity",)),
]


@dataclass
class Table:
    columns: list[str]
    rows: list[list[str]]
    caption: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"columns": self.columns, "rows": self.rows, "caption": self.caption}


@dataclass
class Section:
    id: str
    title: str
    basis: str = ""
    intro: str = ""
    narrative: Narrative | None = None
    tables: list[Table] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    children: list[Section] = field(default_factory=list)

    def walk(self) -> list[Section]:
        return [self, *(s for c in self.children for s in c.walk())]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "basis": self.basis,
            "intro": self.intro,
            "narrative": self.narrative.to_dict() if self.narrative else None,
            "tables": [t.to_dict() for t in self.tables],
            "notes": self.notes,
            "items": self.items,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class Report:
    run: RunInfo
    summary: dict[str, Any]
    sections: list[Section]
    findings: list[dict[str, Any]]
    statutes: dict[str, dict[str, Any]]
    decisions: list[dict[str, Any]] = field(default_factory=list)
    graph_summary: dict[str, Any] = field(default_factory=dict)
    schema: str = REPORT_SCHEMA

    def all_sections(self) -> list[Section]:
        return [s for top in self.sections for s in top.walk()]

    def narratives(self) -> list[Narrative]:
        return [s.narrative for s in self.all_sections() if s.narrative is not None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "run": {
                "repo": self.run.repo,
                "commit": self.run.commit,
                "run_id": self.run.run_id,
                "repo_url": self.run.repo_url,
                "path_prefix": self.run.path_prefix,
                "generated_at": self.run.generated_at,
            },
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections],
            "findings": self.findings,
            "statutes": self.statutes,
            "decisions": self.decisions,
            "graph_summary": self.graph_summary,
        }


def cite(ids: Sequence[str]) -> str:
    return " ".join(f"[{i}]" for i in dict.fromkeys(ids))


def likelihood(f: Finding, ctx: ReportContext) -> str:
    """Likelihood that the processing happens: remote when no entry point reaches it, probable
    when dynamic verification saw it happen, possible when only static analysis says so."""
    if not f.reachable:
        return "remote"
    if (ctx.verification(f) or {}).get("status") == "verified":
        return "probable"
    return "possible"


def overall_risk(likely: str, harm: str) -> str:
    score = _LEVEL[likely] * _LEVEL[harm]
    return "high" if score >= 6 else "medium" if score >= 3 else "low"


class DPIABuilder:
    def __init__(
        self,
        ctx: ReportContext,
        llm: LLMDrafter | None = None,
    ) -> None:
        self.ctx = ctx
        self.template = TemplateDrafter()
        self.llm = llm
        self.findings = ctx.findings.findings
        self.briefs = {f.id: ctx.brief(f) for f in self.findings}
        for f in self.findings:
            self.briefs[f.id]["likelihood"] = likelihood(f, ctx)

    # --- helpers --------------------------------------------------------------------------

    def _narrate(
        self, section_id: str, title: str, instruction: str, findings: Sequence[Finding]
    ) -> Narrative:
        brief = SectionBrief(section_id, title, instruction, [self.briefs[f.id] for f in findings])
        return draft_section(brief, self.template, self.llm)

    def _where(self, *categories: str) -> list[Finding]:
        return [f for f in self.findings if f.category in categories]

    # --- sections -------------------------------------------------------------------------

    def build(self) -> Report:
        sections = [
            self.screening(),
            self.description(),
            Section(
                "consultation",
                "3. Consultation",
                basis="ICO DPIA template step 3; GDPR Art. 35(9)",
                notes=[
                    "Not determinable from code. The controller records whom it consulted "
                    "(data subjects or their representatives, processors, security experts) "
                    "and what they said."
                ],
            ),
            self.necessity(),
            self.risks(),
            self.measures(),
            self.signoff(),
            self.cpra(),
            self.unresolved(),
            self.coverage(),
        ]
        return Report(
            run=self.ctx.run,
            summary=self.summary(),
            sections=sections,
            findings=[self.appendix_entry(f) for f in self.findings],
            statutes={
                ref.id: ref.to_dict()
                for ref in self.ctx.statutes.resolve(
                    sorted({r for f in self.findings for r in f.statute_refs})
                )
            },
            decisions=[d.to_dict() for d in self.ctx.records],
            graph_summary=self.ctx.graph.summary,
        )

    def summary(self) -> dict[str, Any]:
        by_severity: dict[str, int] = defaultdict(int)
        for f in self.findings:
            by_severity[f.severity] += 1
        first = self.findings[0] if self.findings else None
        return {
            "findings": len(self.findings),
            "resolved": len(self.ctx.findings.resolved),
            "unresolved": len(self.ctx.findings.unresolved),
            "by_severity": dict(by_severity),
            "threshold": self.ctx.findings.threshold,
            "severity_version": self.ctx.findings.severity_version,
            "statutes_version": self.ctx.statutes.version,
            "question_set_version": first.question_set_version if first else "",
            "provider": first.provider if first else "",
            "provider_version": first.provider_version if first else "",
        }

    def screening(self) -> Section:
        rows = []
        checks = [
            (
                "Special category data (GDPR Art. 9)",
                [f.id for f in self.findings if self.briefs[f.id]["special_category"]],
                "GDPR Art. 35(3)(b)",
            ),
            (
                "Data concerning children",
                [f.id for f in self.findings if self.briefs[f.id]["relates_to_minor"]],
                "EDPB WP248 rev.01, criterion 7",
            ),
            (
                "Precise location data",
                [f.id for f in self.findings if "precise_location" in f.data_categories],
                "EDPB WP248 rev.01, criterion 4",
            ),
            (
                "Disclosure to third parties",
                [f.id for f in self.findings if f.reachable and self.briefs[f.id]["third_party"]],
                "ICO screening checklist",
            ),
            (
                "Possible sale or sharing (CPRA)",
                [f.id for f in self._where("sale_or_share_candidate")],
                "Cal. Civ. Code § 1798.140(ad), (ah)",
            ),
            (
                "Storage outside the data subjects' jurisdiction unclear",
                [f.id for f in self._where("cross_border_ambiguity")],
                "GDPR Arts. 44-46",
            ),
        ]
        for label, ids, basis in checks:
            rows.append([label, "yes" if ids else "not found in code", cite(ids), basis])
        return Section(
            "screening",
            "1. Need for a DPIA",
            basis="GDPR Art. 35(1), (3); ICO DPIA template step 1",
            intro="Screening criteria found in the code. A criterion marked 'not found in code' "
            "may still apply for reasons the code does not show.",
            tables=[Table(["Criterion", "Present", "Findings", "Basis"], rows)],
        )

    def description(self) -> Section:
        g = self.ctx.graph
        reachable = [f for f in self.findings if f.reachable]
        nature_rows = []
        for f in self.findings:
            if f.category in (
                "personal_data_to_third_party",
                "personal_data_processing",
                "logging_of_personal_data",
                "unresolved_flow",
                "observed_unexpected_destination",
                "unreachable_flow",
            ):
                b = self.briefs[f.id]
                v = b["verification"] or {}
                nature_rows.append(
                    [
                        ", ".join(CATEGORY_LABELS.get(c, c) for c in f.data_categories) or "-",
                        ", ".join(b["entry_points"]) or "-",
                        b["destination"] or "-",
                        v.get("status", "not run"),
                        cite([f.id]),
                    ]
                )
        summary = g.summary
        stats = [
            ["Languages", ", ".join(g.languages)],
            ["Entry points", str(summary.get("entry_points", 0))],
            ["Personal-data sources", str(summary.get("nodes", {}).get("source", 0))],
            ["Sinks", str(summary.get("nodes", {}).get("sink", 0))],
            ["Source-to-sink paths", str(summary.get("flows", len(g.flows)))],
            ["Reachable paths", str(summary.get("reachable_flows", 0))],
            [
                "Data categories",
                ", ".join(
                    sorted(
                        {CATEGORY_LABELS.get(c, c) for f in reachable for c in f.data_categories}
                    )
                )
                or "-",
            ],
        ]
        grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for f in self.findings:
            p = self.briefs[f.id]["purpose"]
            if p and f.category not in ("unreachable_flow",):
                key = (
                    self.briefs[f.id]["destination"] or "-",
                    PURPOSE_LABELS.get(p["answer"], "could not be determined"),
                    f"{p['probability']:.2f}",
                )
                grouped[key].append(f.id)
        purpose_rows = [[*key, cite(ids)] for key, ids in grouped.items()]
        transfer = self._where(
            "personal_data_to_third_party",
            "personal_data_processing",
            "logging_of_personal_data",
            "unresolved_flow",
            "observed_unexpected_destination",
            "unreachable_flow",
        )
        return Section(
            "description",
            "2. Description of the processing",
            basis="GDPR Art. 35(7)(a); ICO DPIA template step 2",
            children=[
                Section(
                    "description.nature",
                    "Nature of the processing",
                    narrative=self._narrate(
                        "description.nature",
                        "Nature of the processing",
                        "Describe how personal data is collected, used, stored, and disclosed.",
                        transfer,
                    ),
                    tables=[
                        Table(
                            [
                                "Data",
                                "Collected at",
                                "Destination",
                                "Dynamic verification",
                                "Finding",
                            ],
                            nature_rows,
                        )
                    ],
                ),
                Section(
                    "description.scope",
                    "Scope of the processing",
                    narrative=self._narrate(
                        "description.scope",
                        "Scope of the processing",
                        "Describe the categories of personal data involved, including special "
                        "category data.",
                        self.findings,
                    ),
                    tables=[Table(["Measure", "Value"], stats, caption="Graph statistics")],
                ),
                Section(
                    "description.context",
                    "Context of the processing",
                    narrative=self._narrate(
                        "description.context",
                        "Context of the processing",
                        "Describe where personal data enters the application and where "
                        "recipients process it.",
                        self.findings,
                    ),
                    notes=[
                        "The relationship with data subjects, their expectations, and any prior "
                        "concerns are not visible in code."
                    ],
                ),
                Section(
                    "description.purposes",
                    "Purposes of the processing",
                    narrative=self._narrate(
                        "description.purposes",
                        "Purposes of the processing",
                        "State the purpose of each processing activity as the findings indicate.",
                        self.findings,
                    ),
                    tables=[
                        Table(
                            ["Recipient", "Apparent purpose", "Probability", "Finding"],
                            purpose_rows,
                        )
                    ],
                    notes=[
                        "Purposes are inferred from code by the decision provider. The "
                        "controller confirms them."
                    ],
                ),
            ],
        )

    def necessity(self) -> Section:
        rows = []
        for question, categories in CONTROLLER_QUESTIONS:
            ids = [f.id for f in self._where(*categories)]
            rows.append([question, cite(ids) or "-", ""])
        relevant = self._where(
            "indefinite_retention",
            "logging_of_personal_data",
            "reversible_obfuscation",
            "mitigated_partially",
            "cross_border_ambiguity",
            "personal_data_to_third_party",
            "special_category_processing",
            "unreachable_flow",
            "observed_unexpected_destination",
        )
        return Section(
            "necessity",
            "4. Necessity and proportionality",
            basis="GDPR Art. 35(7)(b); Art. 5; ICO DPIA template step 4",
            narrative=self._narrate(
                "necessity",
                "Necessity and proportionality",
                "Describe what the findings show about data minimisation, storage limitation, "
                "security, and transfers.",
                relevant,
            ),
            tables=[
                Table(
                    ["Question for the controller", "Related findings", "Answer"],
                    rows,
                    caption="ICO step 4 questions the code cannot answer",
                )
            ],
        )

    def risks(self) -> Section:
        rows = []
        for n, f in enumerate(self.findings, 1):
            harm = SEVERITY_TO_HARM[f.severity]
            likely = self.briefs[f.id]["likelihood"]
            rows.append([f"R{n}", f.title, cite([f.id]), likely, harm, overall_risk(likely, harm)])
        return Section(
            "risks",
            "5. Risk register",
            basis="GDPR Art. 35(7)(c); ICO DPIA template step 5",
            intro="Likelihood is estimated from the evidence: remote when no entry point reaches "
            "the flow, probable when dynamic verification observed it, and possible when "
            "only static analysis shows it. Severity of harm maps Lantern's severity (minimal for "
            "informational and low, significant for medium, severe for high and critical). "
            "Overall risk is likelihood times severity on a 3x3 scale (Lantern's scale, not "
            "the ICO's).",
            narrative=self._narrate(
                "risks",
                "Risk summary",
                "Summarize the highest risks and how many lower risks remain.",
                self.findings,
            ),
            tables=[
                Table(
                    [
                        "Risk",
                        "Description",
                        "Finding",
                        "Likelihood",
                        "Severity of harm",
                        "Overall risk",
                    ],
                    rows,
                )
            ],
        )

    def measures(self) -> Section:
        rows = []
        for n, f in enumerate(self.findings, 1):
            rows.append([f"R{n}", cite([f.id]), measure_for(self.briefs[f.id]), "", "", ""])
        actionable = [
            f
            for f in self.findings
            if f.severity != "informational" or f.category == "unreachable_flow"
        ]
        return Section(
            "measures",
            "6. Measures to reduce risk",
            basis="GDPR Art. 35(7)(d); ICO DPIA template step 6",
            narrative=self._narrate(
                "measures", "Measures", "Recommend a measure for each finding.", actionable
            ),
            tables=[
                Table(
                    [
                        "Risk",
                        "Finding",
                        "Options to reduce or eliminate risk",
                        "Effect on risk",
                        "Residual risk",
                        "Measure approved",
                    ],
                    rows,
                )
            ],
            notes=["Effect, residual risk, and approval are for the controller to complete."],
        )

    def signoff(self) -> Section:
        return Section(
            "signoff",
            "7. Sign-off and outcomes",
            basis="ICO DPIA template step 7",
            tables=[Table(["Item", "Name and date", "Notes"], [[r, "", ""] for r in SIGNOFF_ROWS])],
        )

    # --- CPRA ---------------------------------------------------------------------------

    def cpra(self) -> Section:
        reachable = [f for f in self.findings if f.reachable]
        cat_rows: dict[str, list[str]] = defaultdict(list)
        lantern_cats: dict[str, set[str]] = defaultdict(set)
        for f in reachable:
            for c in f.data_categories:
                for label in CCPA_CATEGORIES.get(c, []):
                    cat_rows[label].append(f.id)
                    lantern_cats[label].add(CATEGORY_LABELS.get(c, c))
                if c in SENSITIVE_PI:
                    label = "(L) Sensitive personal information"
                    cat_rows[label].append(f.id)
                    lantern_cats[label].add(CATEGORY_LABELS.get(c, c))
        categories = Table(
            ["Category (§ 1798.140(v)(1))", "Data found", "Findings"],
            [[k, ", ".join(sorted(lantern_cats[k])), cite(v)] for k, v in sorted(cat_rows.items())],
        )
        sources: dict[str, list[str]] = defaultdict(list)
        for f in reachable:
            for s in self.ctx.sources(f):
                kind = str(s.attrs.get("source_kind") or "")
                if kind == "orm_read":
                    label = f"The business's own records ({s.attrs.get('model') or 'database'})"
                elif (s.attrs.get("entry") or {}).get("kind") == "route":
                    e = s.attrs["entry"]
                    label = f"Directly from the consumer: {e.get('method')} {e.get('path') or '/'}"
                else:
                    label = f"Application input ({kind.replace('_', ' ') or 'unknown'})"
                sources[label].append(f.id)
        purposes: dict[tuple[str, str], list[str]] = defaultdict(list)
        third: dict[str, list[str]] = defaultdict(list)
        third_info: dict[str, tuple[str, str, str]] = {}
        for f in reachable:
            b = self.briefs[f.id]
            if b["purpose"]:
                purposes[
                    (
                        b["destination"] or "-",
                        PURPOSE_LABELS.get(b["purpose"]["answer"], "could not be determined"),
                    )
                ].append(f.id)
            if b["third_party"] and b["destination"]:
                third[b["destination"]].append(f.id)
                claims = b["vendor_claims"] or {}
                sp = claims.get("cpra_service_provider")
                third_info[b["destination"]] = (
                    f.destination_class or "",
                    "yes" if sp is True else "no" if sp is False else "unknown",
                    ", ".join(b["vendor_regions"]) or "unknown",
                )
        sale_grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for f in reachable:
            sale = self.briefs[f.id]["sale_or_share"]
            if sale and self.briefs[f.id]["third_party"]:
                key = (
                    self.briefs[f.id]["destination"] or "-",
                    sale["answer"],
                    f"{sale['probability']:.2f}",
                )
                sale_grouped[key].append(f.id)
        sale_rows = [[*key, cite(ids)] for key, ids in sorted(sale_grouped.items())]
        spi: dict[str, list[str]] = defaultdict(list)
        for f in reachable:
            for c in f.data_categories:
                if c in SENSITIVE_PI:
                    spi[SENSITIVE_PI[c]].append(f.id)
        retention_rows = []
        for f in self._where("indefinite_retention", "personal_data_processing"):
            r = self.ctx.retention(f)
            if r is None:
                continue
            retention_rows.append(
                [
                    str(r["store"] or "-"),
                    ", ".join(CATEGORY_LABELS.get(c, c) for c in f.data_categories),
                    r["has_expiry"] or "unknown",
                    r["plausible_retention"] or "unknown",
                    ", ".join(k.replace("_", " ") for k in r["evidence_kinds"]) or "none found",
                    cite([f.id]),
                ]
            )
        sale_findings = [
            f
            for f in reachable
            if self.briefs[f.id]["third_party"]
            and (self.briefs[f.id]["sale_or_share"] or f.category == "sale_or_share_candidate")
        ]
        return Section(
            "cpra",
            "8. California: CCPA as amended by the CPRA",
            basis="Cal. Civ. Code §§ 1798.100-1798.199.100; 11 CCR § 7000 et seq.",
            intro="The disclosures a business must make at or before collection (§ 1798.100) and "
            "in its privacy policy (11 CCR § 7011), as far as the code shows them.",
            children=[
                Section(
                    "cpra.categories", "Categories of personal information", tables=[categories]
                ),
                Section(
                    "cpra.sources",
                    "Sources of personal information",
                    tables=[
                        Table(
                            ["Source", "Findings"],
                            [[k, cite(v)] for k, v in sorted(sources.items())],
                        )
                    ],
                ),
                Section(
                    "cpra.purposes",
                    "Business or commercial purposes",
                    tables=[
                        Table(
                            ["Recipient", "Apparent purpose", "Findings"],
                            [[d, p, cite(v)] for (d, p), v in sorted(purposes.items())],
                        )
                    ],
                    notes=[
                        "Whether each purpose is a business purpose under § 1798.140(e) is for "
                        "the business to confirm."
                    ],
                ),
                Section(
                    "cpra.third_parties",
                    "Categories of third parties and recipients",
                    tables=[
                        Table(
                            [
                                "Recipient",
                                "Class",
                                "Registry: service-provider terms",
                                "Processing locations",
                                "Findings",
                            ],
                            [[d, *third_info[d], cite(v)] for d, v in sorted(third.items())],
                        )
                    ],
                    notes=[
                        "'Registry: service-provider terms' is the vendor's own public claim, "
                        "recorded in Lantern's SDK registry; it is not a legal determination."
                    ],
                ),
                Section(
                    "cpra.sale_share",
                    "Sale or sharing determinations",
                    basis="Cal. Civ. Code §§ 1798.120, 1798.135, 1798.140(ad), (ag), (ah)",
                    narrative=self._narrate(
                        "cpra.sale_share",
                        "Sale or sharing",
                        "State which disclosures could be a sale or sharing under the CPRA and "
                        "why.",
                        sale_findings,
                    ),
                    tables=[
                        Table(
                            [
                                "Recipient",
                                "Provider answer: sale or share",
                                "Probability",
                                "Findings",
                            ],
                            sale_rows,
                        )
                    ],
                ),
                Section(
                    "cpra.sensitive",
                    "Sensitive personal information",
                    basis="Cal. Civ. Code §§ 1798.121, 1798.140(ae)",
                    narrative=self._narrate(
                        "cpra.sensitive",
                        "Sensitive personal information",
                        "State which sensitive personal information is processed.",
                        reachable,
                    ),
                    tables=[
                        Table(
                            ["Sensitive personal information", "Findings"],
                            [[k, cite(v)] for k, v in sorted(spi.items())],
                        )
                    ],
                ),
                Section(
                    "cpra.retention",
                    "Retention",
                    basis="Cal. Civ. Code § 1798.100(a)(3)",
                    tables=[
                        Table(
                            [
                                "Store",
                                "Data",
                                "Expiry in code",
                                "Plausible retention",
                                "Evidence",
                                "Findings",
                            ],
                            retention_rows,
                        )
                    ],
                ),
            ],
        )

    # --- unresolved ----------------------------------------------------------------------

    def _questions(self) -> dict[str, str]:
        version = self.findings[0].question_set_version if self.findings else "v1"
        bundle = load_bundle(version or "v1")
        out: dict[str, str] = {}
        for qs in bundle.sets.values():
            for q in qs.questions:
                text = " ".join(q.description.split())
                out[f"{qs.id}.{q.id}"] = (
                    text[: text.index("?") + 1]
                    if "?" in text
                    else (split_sentences(text) or [text])[0]
                )
        return out

    def _step_instruction(self, f: Finding, step: dict[str, Any]) -> str:
        where = f"{step.get('file')}:{step.get('line')}"
        note = str(step.get("note") or "")
        fields = self.briefs[f.id]["fields"]
        what = ", ".join(fields) if fields else "the personal data"
        dest = self.briefs[f.id]["destination"] or "the destination"
        m = re.search(r"non-literal key `([^`]+)`", note)
        if m:
            return (
                f"Determine the runtime value of `{m.group(1)}` and whether the lookup at "
                f"{where} selects {what}. If it does, {what} reaches {dest}."
            )
        m = re.search(r"`([^`]+)`", note)
        if "getattr" in note or "attribute" in note:
            return (
                f"Determine which attribute the dynamic lookup at {where} reads"
                f"{' (' + m.group(1) + ')' if m else ''} and whether it is {what}."
            )
        if "callback" in note or "call through" in note or "parameter" in note:
            return (
                f"Determine which function is called at {where} and whether it passes {what} "
                f"on to {dest}."
            )
        return (
            f"Check the step at {where} ({note or step.get('kind')}) and confirm whether the "
            f"value that reaches {dest} carries {what}."
        )

    def _dynamic_instruction(self, f: Finding) -> str | None:
        v = self.ctx.verification(f)
        if not v:
            return None
        if v.get("status") == "verified":
            seen = ", ".join(v.get("canaries") or [])
            where = ", ".join(v.get("hosts") or []) or "the application's output"
            return (
                f"Dynamic verification observed synthetic {seen} values at {where}. That "
                f"indicates the flow is real; confirm it and record it as resolved."
            )
        return (
            f"Dynamic verification did not confirm this flow (reason: {v.get('reason')}). "
            f"That does not rule it out."
        )

    def _reason_instruction(self, reason: UnresolvedReason, questions: dict[str, str]) -> str:
        node = self.ctx.graph.nodes.get(reason.target_id)
        where = f" for {node.symbol} at {node.file}:{node.line_start}" if node else ""
        alternatives = sorted(
            ((k, v) for k, v in reason.distribution.items() if k != reason.answer),
            key=lambda kv: -kv[1],
        )
        alt = (
            f"; next most likely '{alternatives[0][0]}' ({alternatives[0][1]:.2f})"
            if alternatives
            else ""
        )
        question = questions.get(reason.question, reason.question)
        return (
            f"Answer \"{question}\"{where}. The decision provider answered '{reason.answer}' "
            f"with probability {reason.probability:.2f}, below the "
            f"{self.ctx.findings.threshold:.2f} threshold{alt}."
        )

    def unresolved(self) -> Section:
        questions = self._questions()
        items: list[dict[str, Any]] = []
        for f in self.ctx.findings.unresolved:
            steps = [
                s
                for e in (self.ctx.edge(i) for i in f.edge_ids)
                if e
                for s in (x.to_dict() for x in e.evidence)
                if s.get("unresolved")
            ]
            steps = steps or list(f.evidence.get("unresolved_steps", []))
            evidence = []
            for s in steps:
                evidence.append(
                    {**s, "url": self.ctx.linker.url(str(s.get("file")), int(s.get("line") or 0))}
                )
            instructions = [self._step_instruction(f, s) for s in steps]
            instructions += [self._reason_instruction(r, questions) for r in f.unresolved_reasons]
            dynamic = self._dynamic_instruction(f)
            if dynamic:
                instructions.append(dynamic)
            sink = self.ctx.primary_sink(f)
            items.append(
                {
                    "finding": f.id,
                    "title": f.title,
                    "category": f.category,
                    "severity": f.severity,
                    "flow": {
                        "sources": [
                            f"{s.symbol} ({s.file}:{s.line_start})" for s in self.ctx.sources(f)
                        ],
                        "sink": f"{sink.symbol} ({sink.file}:{sink.line_start})" if sink else None,
                        "sink_url": self.ctx.linker.url(sink.file, sink.line_start)
                        if sink
                        else None,
                        "destination": self.briefs[f.id]["destination"],
                    },
                    "evidence": evidence,
                    "decisions": [r.to_dict() for r in f.unresolved_reasons],
                    "verification": self.ctx.verification(f),
                    "instructions": instructions,
                }
            )
        by_target: dict[str, list[UnresolvedReason]] = defaultdict(list)
        for r in self.ctx.findings.unresolved_decisions:
            owner = next(
                (i for i in items if r.target_id in self.ctx.finding(i["finding"]).node_ids), None
            )
            if owner is not None:
                owner["decisions"].append(r.to_dict())
                owner["instructions"].insert(
                    -1 if owner["verification"] else len(owner["instructions"]),
                    self._reason_instruction(r, questions),
                )
            else:
                by_target[r.target_id].append(r)
        for target_id, reasons in by_target.items():
            node = self.ctx.graph.nodes.get(target_id)
            items.append(
                {
                    "finding": None,
                    "title": "Low-confidence decisions on a reachable flow",
                    "category": "unresolved_decision",
                    "severity": "",
                    "flow": {
                        "sources": [],
                        "sink": f"{node.symbol} ({node.file}:{node.line_start})"
                        if node
                        else target_id,
                        "sink_url": self.ctx.linker.url(node.file, node.line_start)
                        if node
                        else None,
                        "destination": destination_name(node)
                        if node and node.kind == "sink"
                        else None,
                    },
                    "evidence": [],
                    "decisions": [r.to_dict() for r in reasons],
                    "verification": None,
                    "instructions": [self._reason_instruction(r, questions) for r in reasons],
                }
            )
        return Section(
            "unresolved",
            "9. Unresolved flows and decisions",
            intro="Flows the analyzer could not fully resolve and decisions below the confidence "
            "threshold. Each needs a human answer before the assessment is complete.",
            items=items,
            notes=[] if items else ["Nothing is unresolved."],
        )

    # --- coverage ------------------------------------------------------------------------

    def coverage(self) -> Section:
        g = self.ctx.graph
        cov = g.summary.get("coverage", {})
        tainted = int(cov.get("tainted_paths", len(g.flows)))
        unresolved = int(cov.get("paths_with_unresolved_step", 0))
        resolved_fraction = cov.get(
            "resolved_fraction", 1.0 if tainted == 0 else 1 - unresolved / tainted
        )
        deps = g.summary.get("dependencies", {})
        dynamic = g.summary.get("dynamic")
        static_rows = [
            ["Tainted paths", str(tainted)],
            ["Fully resolved paths", f"{tainted - unresolved} ({float(resolved_fraction):.1%})"],
            ["Paths with an unresolved step", str(unresolved)],
            ["Depth-limit truncations", str(cov.get("depth_truncations", 0))],
            ["Parse errors", str(cov.get("parse_errors", 0))],
            [
                "Files by language",
                ", ".join(f"{k}: {v}" for k, v in sorted(g.summary.get("files", {}).items()))
                or "-",
            ],
        ]
        dep_rows = [
            ["Dependencies analyzed", str(deps.get("analyzed", 0))],
            ["Dependencies skipped", str(deps.get("skipped", 0))],
            ["Analysis depth", str(deps.get("depth", 0))],
        ]
        dyn_rows = [["Dynamic verification ran", "yes" if dynamic else "no"]]
        sink_rows = []
        if dynamic:
            counts = dynamic.get("counts", {})
            dyn_rows += [
                ["Status", str(dynamic.get("status"))],
                ["Backend", str(dynamic.get("backend"))],
                ["Modes run", ", ".join(s["mode"] for s in dynamic.get("steps", [])) or "none"],
                ["Requests observed", str(dynamic.get("requests", 0))],
                ["Sinks verified", str(counts.get("verified", 0))],
                ["Sinks inferred", str(counts.get("inferred", 0))],
                ["Unexpected destinations", str(counts.get("observed-unexpected", 0))],
            ]
        for node in sorted(
            (n for n in g.nodes.values() if n.kind == "sink"), key=lambda n: (n.file, n.line_start)
        ):
            d = node.attrs.get("dynamic") or {}
            location = (
                f"{node.file}:{node.line_start}" if node.file != "<dynamic>" else "(dynamic only)"
            )
            sink_rows.append(
                [
                    node.symbol,
                    location,
                    destination_name(node),
                    d.get("status", "inferred" if not dynamic else "-"),
                    d.get("reason", "dynamic verification not run" if not dynamic else "-"),
                    ", ".join(d.get("canaries") or []) or "-",
                ]
            )
        return Section(
            "coverage",
            "10. Coverage",
            intro="What the analysis could and could not see. Sinks are 'verified' only when "
            "dynamic verification observed a canary value reaching them; every other sink "
            "is inferred from the code.",
            tables=[
                Table(["Static analysis", "Value"], static_rows),
                Table(["Dependencies", "Value"], dep_rows),
                Table(["Dynamic verification", "Value"], dyn_rows),
                Table(
                    ["Sink", "Location", "Destination", "Status", "Reason", "Canaries observed"],
                    sink_rows,
                    caption="Sinks: verified versus inferred",
                ),
            ],
        )

    # --- appendix ------------------------------------------------------------------------

    def appendix_entry(self, f: Finding) -> dict[str, Any]:
        link = self.ctx.linker.url
        nodes = []
        for node_id in f.node_ids:
            n = self.ctx.graph.nodes[node_id]
            nodes.append(
                {
                    "id": n.id,
                    "kind": n.kind,
                    "symbol": n.symbol,
                    "file": n.file,
                    "line_start": n.line_start,
                    "line_end": n.line_end,
                    "url": link(n.file, n.line_start),
                    "snippet": n.snippet,
                }
            )
        edges = []
        for edge_id in f.edge_ids:
            e = self.ctx.edge(edge_id)
            if e is None:
                continue
            edges.append(
                {
                    "id": e.id,
                    "from": e.from_node,
                    "to": e.to_node,
                    "kind": e.kind,
                    "steps": [{**s.to_dict(), "url": link(s.file, s.line)} for s in e.evidence],
                }
            )
        decisions = [
            self.ctx.by_decision_id[d].to_dict()
            for d in f.decision_ids
            if d in self.ctx.by_decision_id
        ]
        return {
            **f.to_dict(),
            "anchor_url": link(
                self.ctx.graph.nodes[f.anchor_id].file, self.ctx.graph.nodes[f.anchor_id].line_start
            ),
            "nodes": nodes,
            "edges": edges,
            "decisions": decisions,
            "likelihood": self.briefs[f.id]["likelihood"],
            "measure": measure_for(self.briefs[f.id]),
            "verification": self.ctx.verification(f),
        }


def build_report(
    graph: DataFlowGraph,
    findings: FindingsResult,
    decisions: Sequence[Any],
    run: RunInfo,
    llm: LLMDrafter | None = None,
) -> Report:
    ctx = ReportContext(graph, findings, decisions, run)
    report = DPIABuilder(ctx, llm).build()
    report.summary["narrative"] = sorted({n.drafter for n in report.narratives()})
    return report

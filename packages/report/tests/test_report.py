"""Report generation from a canary-python run (Prompt 7 acceptance tests)."""

from __future__ import annotations

import io
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest

from lantern_decisions.stub import StubProvider
from lantern_report import statutes
from lantern_report.__main__ import main as cli
from lantern_report.context import ReportContext, RunInfo
from lantern_report.docx_render import render_docx
from lantern_report.dpia import Report, build_report
from lantern_report.narrative import (
    CITATION,
    LLMDrafter,
    SectionBrief,
    TemplateDrafter,
    draft_section,
    split_sentences,
    validate_sentences,
)
from lantern_report.render import render_html, render_json, render_markdown
from lantern_worker.pipeline import PipelineConfig, PipelineResult, run_pipeline
from lantern_worker.store import FileRunStore

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "fixtures" / "canary-python"
SHA = "0123456789abcdef0123456789abcdef01234567"
RUN = RunInfo(
    repo="z-brenner/dpiascanner (canary-python)",
    commit=SHA,
    run_id="run-test",
    repo_url="https://github.com/z-brenner/dpiascanner",
    path_prefix="fixtures/canary-python",
)
NARRATIVE_SECTIONS = {
    "description.nature",
    "description.scope",
    "description.context",
    "description.purposes",
    "necessity",
    "risks",
    "measures",
    "cpra.sale_share",
    "cpra.sensitive",
}
BRIEF_KEYS = {
    "id",
    "category",
    "title",
    "severity",
    "status",
    "reachable",
    "data_categories",
    "fields",
    "fields_by_category",
    "third_party",
    "special_category",
    "relates_to_minor",
    "entry_points",
    "destination",
    "destination_class",
    "vendor_claims",
    "vendor_regions",
    "purpose",
    "sale_or_share",
    "transformations",
    "retention",
    "verification",
    "statutes",
    "evidence",
    "likelihood",
}


@pytest.fixture(scope="module")
def result() -> PipelineResult:
    return run_pipeline(FIXTURE, SHA, PipelineConfig(StubProvider()))


@pytest.fixture(scope="module")
def report(result: PipelineResult) -> Report:
    return build_report(result.graph, result.findings, result.decisions, RUN)


def finding_ids(report: Report) -> set[str]:
    return {f["id"] for f in report.findings}


# --------------------------------------------------------------------------- structure


def test_dpia_structure(report: Report) -> None:
    titles = [s.title for s in report.sections]
    assert titles == [
        "1. Need for a DPIA",
        "2. Description of the processing",
        "3. Consultation",
        "4. Necessity and proportionality",
        "5. Risk register",
        "6. Measures to reduce risk",
        "7. Sign-off and outcomes",
        "8. California: CCPA as amended by the CPRA",
        "9. Unresolved flows and decisions",
        "10. Coverage",
    ]
    ids = {s.id for s in report.all_sections()}
    assert {
        "description.nature",
        "description.scope",
        "description.context",
        "description.purposes",
    } <= ids
    assert {
        "cpra.categories",
        "cpra.sources",
        "cpra.purposes",
        "cpra.third_parties",
        "cpra.sale_share",
        "cpra.sensitive",
        "cpra.retention",
    } <= ids
    signoff = next(s for s in report.sections if s.id == "signoff")
    assert all(row[1] == "" and row[2] == "" for row in signoff.tables[0].rows)


def test_template_fallback_produces_a_complete_report(report: Report) -> None:
    narratives = {n.section_id: n for n in report.narratives()}
    assert set(narratives) == NARRATIVE_SECTIONS
    for section_id, narrative in narratives.items():
        assert narrative.drafter == "template"
        assert narrative.sentences, section_id
        assert validate_sentences(narrative.sentences, finding_ids(report)) == [], section_id
    assert report.summary["narrative"] == ["template"]


def test_first_party_logs_are_not_third_parties(report: Report) -> None:
    cpra = next(s for s in report.sections if s.id == "cpra")
    third = next(c for c in cpra.children if c.id == "cpra.third_parties")
    recipients = [row[0] for row in third.tables[0].rows]
    assert "application logs" not in recipients
    assert {"Mixpanel, Inc.", "Stripe, Inc.", "hooks.partner-crm.example"} <= set(recipients)


def test_statute_references_resolve(report: Report) -> None:
    for f in report.findings:
        assert set(f["statute_refs"]) <= set(report.statutes), f["id"]


def test_state_law_provider_hook() -> None:
    class Virginia:
        def lookup(self, category: str, data_categories: Any, destination: Any) -> list[str]:
            return ["VA-580"] if category == "special_category_processing" else []

    before = list(statutes._PROVIDERS)
    statutes.register_state_law_provider(Virginia())
    try:
        refs = statutes.statute_refs_for("special_category_processing", ["health"], None)
        assert "VA-580" in refs and "GDPR-9" in refs
        assert "VA-580" not in statutes.statute_refs_for("indefinite_retention", [], None)
    finally:
        statutes._PROVIDERS[:] = before


# --------------------------------------------------------------------------- unresolved


def test_unresolved_section_lists_c08(report: Report) -> None:
    section = next(s for s in report.sections if s.id == "unresolved")
    [c08] = [
        item
        for item in section.items
        if any(e.get("file") == "app/partners.py" and e.get("line") == 10 for e in item["evidence"])
    ]
    assert c08["category"] == "unresolved_flow"
    assert c08["flow"]["sink"].endswith("(app/partners.py:11)")
    assert c08["flow"]["destination"] == "hooks.partner-crm.example"
    assert "constants.PARTNER_CONTACT_FIELD" in c08["instructions"][0]
    assert "phone" in c08["instructions"][0]
    assert c08["evidence"][0]["url"].endswith("fixtures/canary-python/app/partners.py#L10")
    # Every item says what a reviewer must check.
    assert all(item["instructions"] for item in section.items)


# --------------------------------------------------------------------------- citations


def _all_text(report: Report) -> str:
    return render_json(report)


def _check_location(url: str | None, file: str, line: int) -> None:
    assert url is not None
    parts = urlsplit(url)
    assert parts.netloc == "github.com"
    path = unquote(parts.path)
    assert path.startswith(f"/z-brenner/dpiascanner/blob/{SHA}/fixtures/canary-python/")
    assert path.endswith(file)
    assert parts.fragment == f"L{line}"
    source = FIXTURE / file
    assert source.exists(), file
    assert 1 <= line <= len(source.read_text().splitlines()), (file, line)


def test_json_citations_resolve_to_findings_nodes_and_lines(report: Report) -> None:
    data = json.loads(render_json(report))
    ids = {f["id"] for f in data["findings"]}
    cited = set(CITATION.findall(json.dumps(data["sections"])))
    assert cited and cited <= ids
    for f in data["findings"]:
        nodes = {n["id"]: n for n in f["nodes"]}
        assert set(f["node_ids"]) == set(nodes)
        for n in nodes.values():
            _check_location(n["url"], n["file"], n["line_start"])
        assert set(f["edge_ids"]) == {e["id"] for e in f["edges"]}
        for e in f["edges"]:
            assert e["from"] in nodes and e["to"] in nodes
            for step in e["steps"]:
                _check_location(step["url"], step["file"], step["line"])
        for d in f["decisions"]:
            assert d["id"] in f["decision_ids"]


def test_markdown_citations_resolve(report: Report) -> None:
    md = render_markdown(report)
    links = set(re.findall(r"\[\[(F-\d{4})\]\]\(#(f-\d{4})\)", md))
    anchors = set(re.findall(r'<a id="(f-\d{4})"></a>', md))
    assert links
    for fid, target in links:
        assert target == fid.lower() and target in anchors
    unlinked = re.findall(r"(?<!\[)\[F-\d{4}\](?!\])", md)
    assert unlinked == []


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        if tag == "a" and values.get("href"):
            self.hrefs.append(str(values["href"]))


def test_html_is_self_contained_and_citations_resolve(report: Report) -> None:
    page = render_html(report)
    assert page.startswith("<!doctype html>")
    assert not re.search(r"<(script|link|img)[^>]+(src|href)=\"https?://", page)
    parser = _Anchors()
    parser.feed(page)
    internal = [h for h in parser.hrefs if h.startswith("#")]
    assert internal and all(h[1:] in parser.ids for h in internal)
    assert f'<details id="{report.findings[0]["id"].lower()}">' in page
    external = [h for h in parser.hrefs if h.startswith("https://github.com/")]
    assert external and all(f"/blob/{SHA}/" in h for h in external)


def test_docx_citations_resolve(report: Report) -> None:
    blob = render_docx(report)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        document = z.read("word/document.xml").decode()
        rels = z.read("word/_rels/document.xml.rels").decode()
    anchors = set(re.findall(r'w:anchor="([^"]+)"', document))
    bookmarks = set(re.findall(r'w:bookmarkStart w:id="\d+" w:name="([^"]+)"', document))
    assert anchors and anchors <= bookmarks
    assert {f["id"].replace("-", "_") for f in report.findings} <= bookmarks
    assert f"/blob/{SHA}/fixtures/canary-python/app/partners.py#L11" in rels
    for heading in ("Description of the processing", "Risk register", "Unresolved flows"):
        assert heading in document


def test_all_formats_render_from_the_cli(result: PipelineResult, tmp_path: Path) -> None:
    store = FileRunStore(tmp_path / "runs")
    store.save_graph("run-cli", result.graph)
    from lantern_worker.pipeline import decision_rows

    store.save_decisions("run-cli", decision_rows("run-cli", result.decisions))
    store.save_findings("run-cli", result.findings)
    out = tmp_path / "out"
    code = cli(
        [
            str(tmp_path / "runs" / "run-cli"),
            "--out",
            str(out),
            "--repo",
            "canary",
            "--repo-url",
            "https://github.com/z-brenner/dpiascanner",
            "--path-prefix",
            "fixtures/canary-python",
        ]
    )
    assert code == 0
    assert {p.name for p in out.iterdir()} == {
        "report.json",
        "report.md",
        "report.html",
        "report.docx",
    }
    data = json.loads((out / "report.json").read_text())
    assert data["schema"] == "lantern.report/v1"
    assert len(data["decisions"]) == len(result.decisions)
    assert "9. Unresolved flows and decisions" in (out / "report.md").read_text()


# --------------------------------------------------------------------------- LLM drafting


class FakeClient:
    """Replies with scripted drafts; records every prompt it receives."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        reply = self.replies.pop(0) if self.replies else self.default
        return reply(user) if callable(reply) else reply

    @staticmethod
    def default(user: str) -> str:
        payload = json.loads(user.split("\n\nYour previous draft")[0])
        ids = [f["id"] for f in payload["findings"]]
        return json.dumps({"sentences": [f"This section is based on the findings [{ids[0]}]."]})


def brief(report: Report, result: PipelineResult | None = None) -> SectionBrief:
    ctx = ReportContext(result.graph, result.findings, result.decisions, RUN) if result else None
    finding = result.findings.findings[0] if result else None
    data = ctx.brief(finding) if ctx and finding else {"id": report.findings[0]["id"]}
    return SectionBrief("necessity", "Necessity", "Describe it.", [data])


def test_llm_draft_accepted_when_every_sentence_cites(report: Report) -> None:
    fid = report.findings[0]["id"]
    client = FakeClient(
        json.dumps(
            {
                "sentences": [
                    f"Data goes to a vendor [{fid}].",
                    f"It is kept indefinitely under Art. 5(1)(e) [{fid}].",
                ]
            }
        )
    )
    narrative = draft_section(brief(report), TemplateDrafter(), LLMDrafter(client))
    assert (narrative.drafter, narrative.attempts) == ("llm", 1)


def test_llm_draft_regenerated_after_a_rejection(report: Report) -> None:
    fid = report.findings[0]["id"]
    client = FakeClient(
        json.dumps({"sentences": ["An uncited claim. Another one."]}),
        json.dumps({"sentences": [f"A cited claim [{fid}]."]}),
    )
    narrative = draft_section(brief(report), TemplateDrafter(), LLMDrafter(client))
    assert (narrative.drafter, narrative.attempts) == ("llm", 2)
    assert "Your previous draft was rejected" in client.prompts[1]
    assert narrative.rejections[0].reason == "no citation"


def test_llm_falls_back_to_template_after_two_failures(
    report: Report, result: PipelineResult
) -> None:
    fid = report.findings[0]["id"]
    client = FakeClient(
        json.dumps({"sentences": [f"Cites a finding that does not exist [F-9999] and [{fid}]."]}),
        "not json at all",
    )
    narrative = draft_section(brief(report, result), TemplateDrafter(), LLMDrafter(client))
    assert narrative.drafter == "template"
    assert narrative.sentences and validate_sentences(narrative.sentences, {fid}) == []
    assert narrative.attempts == 2
    assert [r.reason for r in narrative.rejections] == [
        "cites unknown finding F-9999",
        "unusable reply: JSONDecodeError",
    ]
    assert "fallback" in narrative.note


def test_llm_sees_findings_only_never_code(result: PipelineResult) -> None:
    client = FakeClient()
    report = build_report(result.graph, result.findings, result.decisions, RUN, LLMDrafter(client))
    assert report.summary["narrative"] == ["llm"]
    assert all(n.drafter == "llm" for n in report.narratives())
    snippets = [n.snippet for n in result.graph.nodes.values() if n.snippet]
    steps = [s.text for e in result.graph.edges.values() for s in e.evidence]
    files = {n.file for n in result.graph.nodes.values()}
    for prompt in client.prompts:
        payload = json.loads(prompt)
        for f in payload["findings"]:
            assert set(f) <= BRIEF_KEYS
        for text in [*snippets, *steps]:
            assert text.strip() not in prompt
        for file in files:
            assert file not in prompt
    # The code-only strings that matter most for C08 never leave the report builder.
    assert not any("PARTNER_CONTACT_FIELD" in p or "httpx.post" in p for p in client.prompts)


def test_sentence_splitting_keeps_legal_abbreviations() -> None:
    text = (
        "Storage limitation applies under GDPR Art. 5(1)(e) [F-0001]. A sale is defined in Cal. "
        "Civ. Code § 1798.140(ad) [F-0002]. Uncited sentence."
    )
    parts = split_sentences(text)
    assert len(parts) == 3
    rejections = validate_sentences([text], {"F-0001", "F-0002"})
    assert [r.reason for r in rejections] == ["no citation"]
    assert validate_sentences(["Malformed [F-12] [F-0001]."], {"F-0001"})[0].reason.startswith(
        "malformed"
    )

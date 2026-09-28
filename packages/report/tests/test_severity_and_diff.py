from lantern_report.diff import diff_findings
from lantern_report.findings import Finding
from lantern_report.severity import SeverityInputs, default_table
from lantern_report.statutes import default_map, statute_refs_for


def _inputs(**overrides: object) -> SeverityInputs:
    base: dict[str, object] = {
        "special_category": False,
        "relates_to_minor": False,
        "data_categories": frozenset({"contact"}),
        "destination_class": "analytics",
        "min_identifiability": 5,
        "reachable": True,
    }
    base.update(overrides)
    return SeverityInputs(**base)  # type: ignore[arg-type]


def test_severity_table_is_applied() -> None:
    table = default_table()
    assert table.compute("personal_data_to_third_party", _inputs())[0] == "medium"
    assert (
        table.compute("personal_data_to_third_party", _inputs(destination_class="ad_tech"))[0]
        == "high"
    )
    severity, applied = table.compute(
        "personal_data_to_third_party",
        _inputs(
            destination_class="ad_tech",
            data_categories=frozenset({"health"}),
            special_category=True,
            relates_to_minor=True,
        ),
    )
    assert severity == "critical" and len(applied) == 3
    assert table.compute("indefinite_retention", _inputs(min_identifiability=2))[0] == "low"
    assert table.compute("sale_or_share_candidate", _inputs(reachable=False))[0] == "informational"


def test_every_category_has_a_base_and_statutes() -> None:
    table = default_table()
    statutes = default_map()
    for category, ids in statutes.by_category.items():
        assert category in table.base
        assert all(i in statutes.refs for i in ids)
    refs = statute_refs_for("personal_data_to_third_party", ["precise_location"], "ad_tech")
    assert "CPRA-140-w" in refs and "GDPR-26" in refs
    assert len(refs) == len(set(refs))


def _finding(key: str, severity: str = "medium", dest: str = "analytics") -> Finding:
    return Finding(
        id="F-0001",
        key=key,
        category="personal_data_to_third_party",
        title="t",
        severity=severity,
        severity_modifiers=[],
        status="resolved",
        anchor_id="n",
        node_ids=["n"],
        edge_ids=[],
        flow_ids=[],
        decision_ids=[],
        statute_refs=[],
        reachable=True,
        data_categories=["contact"],
        destination_class=dest,
        classification={"destination_class": dest},
    )


def test_diff_buckets() -> None:
    before = [_finding("K-a"), _finding("K-b"), _finding("K-c"), _finding("K-d")]
    after = [
        _finding("K-b", severity="high"),
        _finding("K-c", dest="ad_tech"),
        _finding("K-d"),
        _finding("K-e"),
    ]
    diff = diff_findings(before, after)
    assert [f.key for f in diff.new] == ["K-e"]
    assert [f.key for f in diff.resolved] == ["K-a"]
    assert [c.key for c in diff.changed_severity] == ["K-b"]
    assert [c.key for c in diff.changed_classification] == ["K-c"]
    assert diff.unchanged == 1

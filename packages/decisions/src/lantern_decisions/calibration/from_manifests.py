"""Build a labeled calibration dataset from the fixture manifests.

States built here carry raw evidence only: the code snippet around the manifest location,
the field name, and the language. They deliberately omit analyzer hints (lexicon matches,
rule ids, registry entries), so the labels cannot leak into the input. A provider that only
reads hints, such as the StubProvider, will score poorly on this dataset; that is the point.

    python -m lantern_decisions.calibration.from_manifests --fixtures fixtures \
        --out packages/decisions/src/lantern_decisions/calibration/data/fixtures_v1.jsonl
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml

from lantern_decisions.calibration.dataset import LabeledExample, save_dataset
from lantern_decisions.question_sets import TargetType
from lantern_decisions.types import STATE_SCHEMA, state_hash

CONTEXT_LINES = 3
VERSION = "v1"


def snippet(root: Path, rel: str, line: int, context: int = CONTEXT_LINES) -> tuple[str, int, int]:
    lines = (root / rel).read_text(encoding="utf-8").splitlines()
    start = max(1, line - context)
    end = min(len(lines), line + context)
    return "\n".join(lines[start - 1 : end]), start, end


def find_line(root: Path, rel: str, needle: str) -> int:
    for number, text in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), 1):
        if needle in text:
            return number
    raise ValueError(f"{needle!r} not found in {rel}")


def _state(
    target_type: TargetType, language: str, root: Path, loc: Mapping[str, Any], **extra: Any
) -> dict[str, Any]:
    line = int(loc["line"]) if "line" in loc else find_line(root, loc["file"], loc["snippet"])
    code, start, end = snippet(root, loc["file"], line)
    state: dict[str, Any] = {
        "schema": STATE_SCHEMA,
        "target_type": target_type.value,
        "language": language,
        "location": {"file": loc["file"], "line_start": line, "line_end": line},
        "snippet": code,
        "snippet_span": [start, end],
    }
    state.update({k: v for k, v in extra.items() if v is not None})
    return state


def examples_for_fixture(fixture_dir: Path) -> Iterator[LabeledExample]:
    manifest = yaml.safe_load((fixture_dir / "MANIFEST.yaml").read_text())
    name, language = manifest["fixture"], manifest["language"]

    def example(
        suffix: str, target: TargetType, state: dict[str, Any], labels: dict[str, str], prov: str
    ) -> LabeledExample:
        return LabeledExample(
            example_id=f"{name}:{suffix}",
            target_type=target,
            question_set_id=target.value,
            question_set_version=VERSION,
            state=state,
            labels=labels,
            provenance=f"{name}:{prov}",
        )

    for flow in manifest["flows"]:
        fid = flow["id"]
        special = "yes" if flow.get("special_category") else "no"
        for source in flow["sources"]:
            state = _state(TargetType.SOURCE, language, fixture_dir, source, field=source["field"])
            labels = {
                "data_category": source["expected_data_category"],
                "special_category_art9": special,
                "relates_to_minor": "no",
            }
            yield example(f"{fid}:source:{source['field']}", TargetType.SOURCE, state, labels, fid)

        sink_state = _state(TargetType.SINK, language, fixture_dir, flow["sink"])
        yield example(
            f"{fid}:sink",
            TargetType.SINK,
            sink_state,
            {"destination_class": flow["sink"]["expected_sink_class"]},
            fid,
        )

        edge_loc = flow.get("transform") or flow.get("opaque_access") or flow["sink"]
        edge_state = _state(
            TargetType.EDGE,
            language,
            fixture_dir,
            edge_loc,
            field=flow["sources"][0]["field"],
        )
        yield example(
            f"{fid}:edge",
            TargetType.EDGE,
            edge_state,
            {"transformation": flow["expected_transformation"]},
            fid,
        )

        if "retention" in flow:
            retention_state = _state(TargetType.RETENTION, language, fixture_dir, flow["sink"])
            evidence = []
            for item in flow["retention"].get("evidence", []):
                line = find_line(fixture_dir, item["file"], item["snippet"])
                code, _, _ = snippet(fixture_dir, item["file"], line, context=1)
                evidence.append({"file": item["file"], "line": line, "text": code})
            if evidence:
                retention_state["nearby_code"] = evidence
            has_expiry = "yes" if flow["retention"]["has_expiry"] else "no"
            yield example(
                f"{fid}:retention",
                TargetType.RETENTION,
                retention_state,
                {"has_expiry": has_expiry},
                fid,
            )

    for index, trap in enumerate(manifest.get("traps", []), 1):
        state = _state(TargetType.SOURCE, language, fixture_dir, trap)
        yield example(
            f"trap{index:02d}:source",
            TargetType.SOURCE,
            state,
            {"data_category": "none", "special_category_art9": "no", "relates_to_minor": "no"},
            f"trap{index:02d}",
        )


def build(fixtures_root: Path) -> list[LabeledExample]:
    seen: set[str] = set()
    out: list[LabeledExample] = []
    for fixture in ("canary-python", "canary-typescript", "clean-python"):
        for ex in examples_for_fixture(fixtures_root / fixture):
            key = f"{ex.target_type}:{state_hash(ex.state)}"
            if key in seen:
                continue
            seen.add(key)
            out.append(ex)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--fixtures", type=Path, default=Path("fixtures"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    examples = build(args.fixtures)
    save_dataset(args.out, examples)
    print(f"wrote {len(examples)} examples to {args.out}")


if __name__ == "__main__":
    main()

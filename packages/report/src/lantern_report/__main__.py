"""Render a stored run as a report.

    python -m lantern_report RUN_DIR --format all --out reports/ \\
        --repo acme/checkout --repo-url https://github.com/acme/checkout

RUN_DIR holds graph.json, findings.json, and decisions.jsonl (``FileRunStore`` layout).
``--llm`` drafts narrative sections with the Anthropic API (ANTHROPIC_API_KEY); the template
drafter is used otherwise and for any section whose draft fails validation twice.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from lantern_analysis.model import DataFlowGraph
from lantern_report.context import RunInfo
from lantern_report.docx_render import render_docx
from lantern_report.dpia import build_report
from lantern_report.findings import FindingsResult
from lantern_report.narrative import AnthropicClient, LLMDrafter
from lantern_report.render import render_html, render_json, render_markdown

FORMATS = ("json", "md", "html", "docx")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a Katz run as a DPIA report.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--format", choices=[*FORMATS, "all"], default="all")
    parser.add_argument("--out", type=Path, default=Path("."))
    parser.add_argument("--repo", default=None, help="display name, e.g. acme/checkout")
    parser.add_argument("--repo-url", default=None, help="https://github.com/<owner>/<repo>")
    parser.add_argument("--path-prefix", default="", help="subdirectory that was scanned")
    parser.add_argument("--llm", action="store_true", help="draft narrative with the Anthropic API")
    args = parser.parse_args(argv)

    graph = DataFlowGraph.from_json((args.run_dir / "graph.json").read_text())
    findings = FindingsResult.from_dict(json.loads((args.run_dir / "findings.json").read_text()))
    decisions = [
        SimpleNamespace(**json.loads(line))
        for line in (args.run_dir / "decisions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    llm = None
    if args.llm:
        client = AnthropicClient.from_env()
        if client is None:
            print("--llm needs ANTHROPIC_API_KEY", file=sys.stderr)
            return 2
        llm = LLMDrafter(client)
    run = RunInfo(
        repo=args.repo or graph.repo,
        commit=graph.commit,
        run_id=args.run_dir.name,
        repo_url=args.repo_url,
        path_prefix=args.path_prefix,
    )
    report = build_report(graph, findings, decisions, run, llm)
    args.out.mkdir(parents=True, exist_ok=True)
    formats = FORMATS if args.format == "all" else (args.format,)
    for fmt in formats:
        path = args.out / f"report.{fmt}"
        if fmt == "docx":
            path.write_bytes(render_docx(report))
        else:
            renderer = {"json": render_json, "md": render_markdown, "html": render_html}[fmt]
            path.write_text(renderer(report), encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""python -m lantern_analysis <repo> [--out graph.json] [--commit SHA] [--depth N]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lantern_analysis.analyze import AnalysisOptions, analyze_repo


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Katz's personal-data flow graph.")
    parser.add_argument("repo", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--commit", default="")
    parser.add_argument("--depth", type=int, default=12)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--track-internal-ids", action="store_true")
    args = parser.parse_args()
    graph = analyze_repo(
        args.repo,
        args.commit,
        AnalysisOptions(
            depth_limit=args.depth,
            exclude=args.exclude,
            track_internal_ids=args.track_internal_ids,
        ),
    )
    text = graph.to_json()
    if args.out:
        args.out.write_text(text)
    else:
        sys.stdout.write(text + "\n")
    print(json.dumps(graph.summary, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()

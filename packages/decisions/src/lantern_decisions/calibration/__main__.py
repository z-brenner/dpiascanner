"""python -m lantern_decisions.calibration [--provider stub|jev] [--dataset path] [--out dir]"""

from __future__ import annotations

import argparse
from pathlib import Path

from lantern_decisions.calibration.dataset import load_dataset
from lantern_decisions.calibration.harness import run_calibration, write_report
from lantern_decisions.factory import PROVIDERS, make_provider


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure a decision provider's calibration.")
    parser.add_argument("--provider", choices=PROVIDERS, default="stub")
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("calibration-report"))
    parser.add_argument("--bins", type=int, default=10)
    args = parser.parse_args()

    report = run_calibration(make_provider(args.provider), load_dataset(args.dataset), args.bins)
    paths = write_report(report, args.out)
    print(
        f"{report.provider} {report.provider_version}: accuracy {report.accuracy:.3f}, "
        f"ECE {report.ece:.3f} over {report.predictions} predictions"
    )
    for kind, path in paths.items():
        print(f"  {kind}: {path}")


if __name__ == "__main__":
    main()

"""CLI for aggregating compatible Dev evaluation reports without merging datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.reliability.reporting import aggregate_reports


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", help="Independent JSON report files")
    parser.add_argument("--output", required=True, help="Aggregate JSON output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    aggregate = aggregate_reports(args.reports)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(f"Aggregate written to {output}")


if __name__ == "__main__":
    main()

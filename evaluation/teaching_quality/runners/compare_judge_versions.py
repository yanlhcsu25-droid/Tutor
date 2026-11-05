"""Run Judge v1 and v2 on fixed historical paper outputs without mutating v1 artifacts."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from calculus_agent.config import Settings  # noqa: E402
from evaluation.teaching_quality.judge.provider import build_siliconflow_judge_adapter  # noqa: E402
from evaluation.teaching_quality.runners.run_dev_eval import (  # noqa: E402
    atomic_json, grade_result, load_dev_cases,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judge-model")
    parser.add_argument("--pacing-seconds", type=float, default=1)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required")
    if args.output.exists():
        parser.error("output already exists")

    rows = [json.loads(line) for line in args.v1_runs.read_text().splitlines() if line.strip()]
    selected: list[dict[str, Any]] = []
    for case_id in ("TQ-PQ-001", "TQ-PQ-002", "TQ-PQ-003"):
        papers = [row for row in rows if row["case_id"] == case_id and row.get("result", {}).get("actual_output", {}).get("paper")]
        selected.extend(papers if case_id == "TQ-PQ-002" else papers[:1])
    cases, _ = load_dev_cases()
    case_by_id = {case["case_id"]: case for case in cases}
    settings = Settings()
    if args.judge_model:
        settings = settings.model_copy(update={"siliconflow_agent_model": args.judge_model})
    adapter = build_siliconflow_judge_adapter(settings, pacing_seconds=args.pacing_seconds)

    comparisons = []
    for row in selected:
        case = case_by_id[row["case_id"]]
        actual = row["result"]["actual_output"]
        grades = {
            version: grade_result(case, actual, adapter, judge_version=version)
            for version in ("v1", "v2")
        }
        scores = {
            version: (grade.get("score") or {}).get("weighted_score")
            for version, grade in grades.items()
        }
        comparisons.append({
            "case_id": row["case_id"],
            "source_run_id": row["run_id"],
            "paper_id": actual["paper"]["id"],
            "scores": scores,
            "v2_minus_v1": (
                scores["v2"] - scores["v1"]
                if scores["v1"] is not None and scores["v2"] is not None else None
            ),
            "grades": grades,
        })
    pq2_deltas = [item["v2_minus_v1"] for item in comparisons if item["case_id"] == "TQ-PQ-002" and item["v2_minus_v1"] is not None]
    output = {
        "kind": "judge_v1_v2_fixed_paper_comparison",
        "created_at": datetime.now(UTC).isoformat(),
        "source_v1_runs": str(args.v1_runs),
        "selection": "all three generated PQ-002 papers; first generated PQ-001 and PQ-003 papers",
        "pq002_mean_delta": statistics.mean(pq2_deltas) if pq2_deltas else None,
        "comparisons": comparisons,
        "judge_tokens": "unavailable" if not any(
            ((grade.get("judge_provider") or {}).get("last_call") or {}).get("usage")
            for item in comparisons for grade in item["grades"].values()
        ) else "available_in_per-grade_provenance",
    }
    atomic_json(args.output, output)
    print(args.output)


if __name__ == "__main__":
    main()

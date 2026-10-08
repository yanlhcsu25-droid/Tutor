# ruff: noqa: E402
"""Run fixed-sample Judge calibration. This never executes Tutor or Blind cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from calculus_agent.config import get_settings
from tests.evals.teaching_quality.grader.behavioral import (
    parse_case_judge_output,
    render_judge_prompt,
)
from tests.evals.teaching_quality.grader.provider import build_siliconflow_judge_adapter
from tests.evals.teaching_quality.grader.semantic import (  # noqa: E402
    ModelBackedSemanticAssertionEvaluator,
)

ROOT = Path(__file__).resolve().parents[1]


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases() -> dict[str, dict[str, Any]]:
    cases = {}
    for path in (ROOT / "cases").glob("*.yaml"):
        for case in yaml.safe_load(path.read_text())["cases"]:
            cases[case["case_id"]] = case
    return cases


def call_metadata(adapter) -> dict[str, Any] | None:
    provenance = adapter.provenance()
    return provenance["last_call"]


def usage_total(results: list[dict[str, Any]]) -> dict[str, int]:
    total = Counter()
    for result in results:
        usage = (result.get("call") or {}).get("usage") or {}
        for source, target in (
            ("prompt_tokens", "prompt_tokens"),
            ("completion_tokens", "completion_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            if isinstance(usage.get(source), int):
                total[target] += usage[source]
    return dict(total)


def run_semantic(adapter) -> dict[str, Any]:
    path = ROOT / "calibration/semantic_contrast_v1.yaml"
    contrast = yaml.safe_load(path.read_text())
    evaluator = ModelBackedSemanticAssertionEvaluator(adapter)
    results = []
    for sample in contrast["samples"]:
        try:
            decision = evaluator.evaluate(
                sample["assertion"], sample["actual_output"], sample["setup"]
            )
            matched = decision.passed == sample["expected_passed"]
            results.append({
                "sample_id": sample["sample_id"],
                "assertion_type": sample["assertion_type"],
                "example_kind": sample["example_kind"],
                "expected_passed": sample["expected_passed"],
                "actual": decision.model_dump(),
                "matched": matched,
                "failure_class": None if matched else "semantic_assertion_error",
                "call": call_metadata(adapter),
            })
        except Exception as exc:
            results.append({
                "sample_id": sample["sample_id"],
                "assertion_type": sample["assertion_type"],
                "expected_passed": sample["expected_passed"],
                "matched": False,
                "failure_class": "provider_error",
                "error": f"{type(exc).__name__}: {exc}",
                "call": call_metadata(adapter),
            })
    matched = sum(result["matched"] for result in results)
    return {
        "fixture": str(path),
        "fixture_sha256": file_sha(path),
        "label_status": contrast["status"],
        "sample_count": len(results),
        "matched": matched,
        "accuracy": matched / len(results),
        "failure_counts": dict(Counter(
            result["failure_class"] for result in results if result["failure_class"]
        )),
        "usage": usage_total(results),
        "results": results,
    }


def inferred_band(scores: dict[str, int], weighted: float) -> str:
    if weighted >= 4 and min(scores.values()) >= 3:
        return "strong"
    if weighted < 3 or min(scores.values()) == 1:
        return "poor"
    return "borderline"


def run_behavioral(adapter) -> dict[str, Any]:
    path = ROOT / "calibration/calibration_set_v1.yaml"
    calibration = yaml.safe_load(path.read_text())
    cases = load_cases()
    results = []
    weighted_by_case: dict[str, dict[str, float]] = defaultdict(dict)
    axis_matches = axis_total = 0
    for entry in calibration["cases"]:
        case = cases[entry["case_id"]]
        weights = case["expected"]["behavioral_rubric"]["axes"]
        for sample in entry["samples"]:
            try:
                payload = adapter.complete(render_judge_prompt(case, sample["actual_output"]))
                judge = parse_case_judge_output(case, dict(payload))
                expected_ranges = sample["expected_quality"]["expected_axis_ranges"]
                matches = {
                    axis: limits[0] <= judge.scores[axis] <= limits[1]
                    for axis, limits in expected_ranges.items()
                }
                weighted = sum(judge.scores[axis] * weight for axis, weight in weights.items())
                band = inferred_band(judge.scores, weighted)
                expected_band = sample["expected_quality"]["overall_band"]
                axis_matches += sum(matches.values())
                axis_total += len(matches)
                weighted_by_case[entry["case_id"]][sample["band"]] = weighted
                failure = None if all(matches.values()) and band == expected_band else "judge_error"
                results.append({
                    "sample_id": sample["sample_id"],
                    "case_id": entry["case_id"],
                    "expected_band": expected_band,
                    "actual_band": band,
                    "band_matched": band == expected_band,
                    "axis_matches": matches,
                    "weighted_score": round(weighted, 4),
                    "judge": judge.model_dump(),
                    "failure_class": failure,
                    "call": call_metadata(adapter),
                })
            except Exception as exc:
                results.append({
                    "sample_id": sample["sample_id"],
                    "case_id": entry["case_id"],
                    "expected_band": sample["expected_quality"]["overall_band"],
                    "band_matched": False,
                    "failure_class": "provider_error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "call": call_metadata(adapter),
                })
    rankings = {
        case_id: scores.get("strong", -1) > scores.get("borderline", -1) > scores.get("poor", -1)
        for case_id, scores in weighted_by_case.items()
    }
    valid = [result for result in results if "actual_band" in result]
    return {
        "fixture": str(path),
        "fixture_sha256": file_sha(path),
        "label_status": calibration["status"],
        "sample_count": len(results),
        "valid_outputs": len(valid),
        "band_accuracy": sum(r["band_matched"] for r in results) / len(results),
        "axis_agreement": axis_matches / axis_total if axis_total else 0,
        "ranking_consistency": sum(rankings.values()) / len(rankings) if rankings else 0,
        "ranking_by_case": rankings,
        "failure_counts": dict(Counter(
            result["failure_class"] for result in results if result["failure_class"]
        )),
        "usage": usage_total(results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["semantic", "behavioral", "all"], default="all")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--pacing-seconds", type=float, default=0.2)
    args = parser.parse_args()

    settings = get_settings()
    adapter = build_siliconflow_judge_adapter(
        settings, max_tokens=args.max_tokens, pacing_seconds=args.pacing_seconds
    )
    report: dict[str, Any] = {
        "version": "1.0",
        "kind": "fixed_sample_dev_judge_calibration",
        "status": "preliminary",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "provider_config": adapter.provenance()["config"],
        "prompt_hashes": {
            path.name: file_sha(path) for path in sorted((ROOT / "prompts").glob("*.md"))
        },
        "blind_executed": False,
        "tutor_executed": False,
    }
    if args.mode in {"semantic", "all"}:
        report["semantic"] = run_semantic(adapter)
    if args.mode in {"behavioral", "all"}:
        report["behavioral"] = run_behavioral(adapter)

    output = args.output or ROOT / "reports" / (
        "judge-calibration-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()

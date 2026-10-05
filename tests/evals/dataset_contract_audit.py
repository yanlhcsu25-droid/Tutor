"""Static contract audit for heterogeneous Teacher Agent eval YAML suites."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASE_DIR = ROOT / "tests" / "evals" / "cases"
LEGAL_STATUSES = {"completed", "needs_clarification", "waiting_confirmation", "failed"}
SUPPORTED_SETUP_KEYS = {
    "generation_plan", "pending_generation", "pending_replacement", "current_paper",
    "teaching_design",
}
SUPPORTED_GRADERS = {
    "state", "score", "workflow_safety", "acceptance", "constraint_preservation",
}
KNOWN_UNSUPPORTED_GRADERS = {
    "semantic", "paper_state", "schema", "error", "trace", "tool_rule", "context",
    "context_stability",
}
DISPOSITION_ORDER = {
    "valid": 0,
    "ambiguous_label": 1,
    "grader_unsupported": 2,
    "fixture_mismatch": 3,
    "runner_unsupported": 4,
}
AMBIGUOUS_ADDRESS = re.compile(r"(?<!全卷)(?<!选择题)(?<!填空题)(?<!计算题)(?<!证明题)第\s*[一二三四五六七八九十\d]+\s*题")
CONFIRM_WORDS = re.compile(r"确认|生成吧|就按这个|应用|提交")
CANCEL_WORDS = re.compile(r"取消|放弃|不要了|不要这个|作废|终止|撤销")


def _finding(kind: str, message: str) -> dict[str, str]:
    return {"kind": kind, "message": message}


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _grader_type(grader: Any) -> str | None:
    if isinstance(grader, str):
        return grader
    return grader.get("type") if isinstance(grader, dict) else None


def _workflow_case_findings(case: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    case_id = str(case.get("id") or "<missing-id>")
    turns = case.get("turns")
    expected = case.get("expected")
    setup = case.get("setup") or {}
    graders = case.get("graders") or []

    if not isinstance(turns, list) or not turns:
        findings.append(_finding("runner_unsupported", "turns must be a non-empty list"))
    elif any(not isinstance(turn, dict) or not isinstance(turn.get("user"), str) or not turn["user"].strip() for turn in turns):
        findings.append(_finding("runner_unsupported", "every turn must contain non-empty user text"))

    if not isinstance(expected, dict):
        findings.append(_finding("runner_unsupported", "expected must be an object for workflow runner"))
    elif expected.get("status") not in LEGAL_STATUSES:
        findings.append(_finding(
            "fixture_mismatch", f"expected.status={expected.get('status')!r} is not legal",
        ))
    elif set(expected) == {"status"}:
        findings.append(_finding(
            "ambiguous_label", "expected asserts only final status; business oracle is missing",
        ))

    if not isinstance(setup, dict):
        findings.append(_finding("runner_unsupported", "setup must be an object"))
    else:
        unsupported = sorted(set(setup) - SUPPORTED_SETUP_KEYS)
        if unsupported:
            findings.append(_finding("runner_unsupported", f"unsupported setup keys: {unsupported}"))
        current_paper = setup.get("current_paper")
        if current_paper is not None and not isinstance(current_paper, dict):
            findings.append(_finding("fixture_mismatch", "setup.current_paper must be an object"))
        elif isinstance(current_paper, dict):
            if not isinstance(current_paper.get("question_count", 10), int) or current_paper.get("question_count", 10) <= 0:
                findings.append(_finding("fixture_mismatch", "current_paper.question_count must be positive"))
            if not isinstance(current_paper.get("total_score", 100), int) or current_paper.get("total_score", 100) <= 0:
                findings.append(_finding("fixture_mismatch", "current_paper.total_score must be positive"))

    if not graders:
        findings.append(_finding("grader_unsupported", "case has no grader"))
    for grader in graders:
        grader_type = _grader_type(grader)
        if grader_type in KNOWN_UNSUPPORTED_GRADERS:
            findings.append(_finding("grader_unsupported", f"grader {grader_type!r} is skipped by runner"))
        elif grader_type not in SUPPORTED_GRADERS:
            findings.append(_finding("grader_unsupported", f"grader {grader_type!r} is unknown"))

    review_status = (case.get("review") or {}).get("status") if isinstance(case.get("review") or {}, dict) else None
    if review_status in {"pending", "needs_review", "ambiguous"}:
        findings.append(_finding("ambiguous_label", f"manual review status is {review_status!r}"))

    user_text = "\n".join(
        str(turn.get("user") or "") for turn in turns or [] if isinstance(turn, dict)
    )
    expected_status = expected.get("status") if isinstance(expected, dict) else None
    if AMBIGUOUS_ADDRESS.search(user_text) and "全卷" not in user_text and expected_status != "needs_clarification":
        findings.append(_finding(
            "ambiguous_label", "unqualified 第N题 addressing is not labelled needs_clarification",
        ))

    safety_graders = [
        grader for grader in graders
        if isinstance(grader, dict) and grader.get("type") == "workflow_safety"
    ]
    if (
        CONFIRM_WORDS.search(user_text)
        and not CANCEL_WORDS.search(user_text)
        and expected_status == "completed"
        and any(
            grader.get("paper_unchanged") is True for grader in safety_graders
        )
    ):
        findings.append(_finding(
            "fixture_mismatch", "confirmation expects completed but paper_unchanged=true",
        ))

    if expected_status == "needs_clarification" and not any(
        _grader_type(grader) in {"acceptance", "state"} for grader in graders
    ):
        findings.append(_finding(
            "grader_unsupported", "clarification case has no structured status/acceptance grader",
        ))

    subtype = str(case.get("subtype") or "")
    required_tools = {
        tool
        for grader in graders
        if isinstance(grader, dict) and grader.get("type") == "acceptance"
        for tool in grader.get("required_tools") or []
    }
    if "cancel" in subtype and "discard_pending_plan" not in required_tools:
        findings.append(_finding(
            "ambiguous_label", "cancel contract does not require discard_pending_plan",
        ))

    # Keep the id in exception messages when this function is reused directly.
    for finding in findings:
        finding["case_id"] = case_id
    return findings


def _disposition(findings: list[dict[str, str]]) -> str:
    return max(
        (finding["kind"] for finding in findings),
        key=lambda kind: DISPOSITION_ORDER[kind],
        default="valid",
    )


def audit_paths(paths: list[Path]) -> dict[str, Any]:
    suites: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    dataset_hash = hashlib.sha256()

    for path in sorted(paths):
        raw_bytes = path.read_bytes()
        dataset_hash.update(path.name.encode())
        dataset_hash.update(b"\0")
        dataset_hash.update(raw_bytes)
        try:
            payload = yaml.safe_load(raw_bytes) or {}
        except yaml.YAMLError as exc:
            suites.append({"path": _display_path(path), "parse_error": str(exc), "case_count": 0})
            continue
        cases = payload.get("cases") if isinstance(payload, dict) else None
        if not isinstance(cases, list):
            suites.append({"path": _display_path(path), "parse_error": "cases must be a list", "case_count": 0})
            continue
        suite_split = (payload.get("suite") or {}).get("split")
        suites.append({
            "path": _display_path(path), "split": suite_split,
            "case_count": len(cases), "parse_error": None,
        })
        for case in cases:
            if not isinstance(case, dict):
                findings = [_finding("runner_unsupported", "case must be an object")]
                case_id = "<invalid-case>"
                case_split = suite_split or "unspecified"
                family = None
            else:
                case_id = str(case.get("id") or "<missing-id>")
                case_split = case.get("split") or suite_split or "unspecified"
                family = (str(case.get("category") or ""), str(case.get("subtype") or ""))
                findings = _workflow_case_findings(case)
                if case_id in seen_ids:
                    findings.append(_finding("runner_unsupported", "duplicate case id across dataset"))
                seen_ids.add(case_id)
            results.append({
                "case_id": case_id,
                "suite": path.name,
                "split": str(case_split),
                "family": family,
                "disposition": _disposition(findings),
                "findings": findings,
            })

    counts = Counter(result["disposition"] for result in results)
    split_counts = Counter(result["split"] for result in results)
    family_splits: dict[tuple[str, str], set[str]] = {}
    for result in results:
        family = result["family"]
        if family is not None and result["split"] in {
            "dev", "development", "holdout_candidate",
        }:
            family_splits.setdefault(family, set()).add(result["split"])
    cross_split_families = sum(len(splits) > 1 for splits in family_splits.values())
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": dataset_hash.hexdigest(),
        "summary": {
            "suite_count": len(suites),
            "case_count": len(results),
            "dispositions": dict(sorted(counts.items())),
            "split_counts": dict(sorted(split_counts.items())),
            "expected_case_count": 300,
            "case_count_matches": len(results) == 300,
            "required_split_matches": (
                split_counts.get("development", 0) + split_counts.get("dev", 0) == 200
                and split_counts.get("holdout_candidate", 0) == 100
            ),
            "cross_split_category_subtype_families": cross_split_families,
        },
        "suites": suites,
        "cases": results,
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Teacher Agent Dataset Contract Audit", "",
        f"- Dataset SHA256: `{report['dataset_sha256']}`",
        f"- Cases found: **{summary['case_count']}** / expected **300**",
        f"- 200 Development + 100 Holdout Candidate: **{'PASS' if summary['required_split_matches'] else 'FAIL'}**",
        f"- Cross-split category/subtype families: **{summary['cross_split_category_subtype_families']}**",
        "",
        "## Dispositions", "",
        "| Disposition | Count |", "| --- | ---: |",
    ]
    for name in ("valid", "fixture_mismatch", "ambiguous_label", "runner_unsupported", "grader_unsupported"):
        lines.append(f"| {name} | {summary['dispositions'].get(name, 0)} |")
    lines.extend(["", "## Suites", "", "| Suite | Split | Cases | Parse |", "| --- | --- | ---: | --- |"])
    for suite in report["suites"]:
        lines.append(
            f"| {Path(suite['path']).name} | {suite.get('split') or 'unspecified'} | "
            f"{suite['case_count']} | {'FAIL' if suite.get('parse_error') else 'PASS'} |"
        )
    lines.extend(["", "## Non-valid cases", "", "| Case | Suite | Disposition | Findings |", "| --- | --- | --- | --- |"])
    for case in report["cases"]:
        if case["disposition"] == "valid":
            continue
        reasons = "; ".join(finding["message"] for finding in case["findings"]).replace("|", "\\|")
        lines.append(f"| {case['case_id']} | {case['suite']} | {case['disposition']} | {reasons} |")
    lines.extend([
        "", "## Scope limitation", "",
        "This is a static contract audit. Semantic labels, real fixture supply, and content correctness still require human review before Holdout freeze.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Teacher Agent eval dataset contracts")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    paths = args.paths or sorted(DEFAULT_CASE_DIR.glob("*.yaml"))
    report = audit_paths(paths)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rendered = markdown(report)
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(rendered, encoding="utf-8")
    if not args.json_output and not args.markdown_output:
        print(rendered, end="")
    return 0 if report["summary"]["case_count_matches"] and not any(
        case["disposition"] != "valid" for case in report["cases"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())

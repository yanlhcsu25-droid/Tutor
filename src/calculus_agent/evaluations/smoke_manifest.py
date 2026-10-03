"""Validate a fixed, reviewable 20-case Development smoke manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

REQUIRED_DISTRIBUTION = {"easy": 6, "medium": 9, "hard": 5}


def dataset_sha(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_dataset_cases(paths: list[Path]) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for case in payload.get("cases") or []:
            if isinstance(case, dict) and case.get("id"):
                case_id = str(case["id"])
                if case_id in cases:
                    raise ValueError(f"duplicate dataset case id: {case_id}")
                cases[case_id] = case
    return cases


def validate_manifest(manifest_path: Path, dataset_paths: list[Path]) -> dict[str, Any]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    selected = manifest.get("cases") or []
    datasets = load_dataset_cases(dataset_paths)
    errors: list[str] = []

    frozen = manifest.get("status") == "frozen"
    if not frozen:
        errors.append("manifest status must be frozen")
    if frozen:
        execution = manifest.get("execution") or {}
        versions = manifest.get("versions") or {}
        freeze_lock = manifest.get("freeze_lock") or {}
        if not execution.get("provider") or not execution.get("model"):
            errors.append("frozen manifest must lock execution.provider and execution.model")
        if not versions.get("fixture") or not versions.get("grader"):
            errors.append("frozen manifest must lock fixture and grader versions")
        if freeze_lock.get("human_reviewed") is not True:
            errors.append("frozen manifest must record human_reviewed=true")
        code_sha = str(freeze_lock.get("code_git_sha") or "")
        if re.fullmatch(r"[0-9a-f]{40}", code_sha) is None:
            errors.append("frozen manifest must lock a full code_git_sha")
    if manifest.get("split") != "development":
        errors.append("smoke manifest must use development split")
    if len(selected) != 20:
        errors.append(f"smoke manifest must contain 20 cases, actual={len(selected)}")

    ids = [str(item.get("case_id")) for item in selected if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        errors.append("smoke manifest contains duplicate case ids")
    missing = sorted(case_id for case_id in ids if case_id not in datasets)
    if missing:
        errors.append(f"case ids missing from datasets: {missing}")

    difficulties = Counter(
        str(item.get("difficulty")) for item in selected if isinstance(item, dict)
    )
    if dict(difficulties) != REQUIRED_DISTRIBUTION:
        errors.append(
            f"difficulty distribution must be {REQUIRED_DISTRIBUTION}, "
            f"actual={dict(difficulties)}"
        )
    for item in selected:
        if not isinstance(item, dict):
            errors.append("every selected case must be an object")
            continue
        case = datasets.get(str(item.get("case_id")))
        if case is not None and case.get("difficulty") != item.get("difficulty"):
            errors.append(
                f"{item.get('case_id')}: manifest difficulty does not match dataset label"
            )

    actual_sha = dataset_sha(dataset_paths)
    if manifest.get("dataset_sha") != actual_sha:
        errors.append("manifest dataset_sha does not match selected dataset files")

    budget = manifest.get("budget") or {}
    for field in ("max_model_calls", "max_total_tokens"):
        if not isinstance(budget.get(field), int) or budget[field] <= 0:
            errors.append(f"budget.{field} must be a positive integer")

    return {
        "valid": not errors,
        "manifest": str(manifest_path),
        "dataset_sha": actual_sha,
        "case_count": len(selected),
        "difficulty_distribution": dict(difficulties),
        "budget": budget,
        "case_ids": ids,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate frozen Development smoke manifest")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("datasets", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_manifest(args.manifest, args.datasets)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

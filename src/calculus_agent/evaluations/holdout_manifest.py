"""Validate a frozen, deduplicated Holdout manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from calculus_agent.evaluations.smoke_manifest import dataset_sha, load_dataset_cases

ROOT = Path(__file__).resolve().parents[3]
COMPONENT_PATHS = {
    "runtime": sorted((ROOT / "src/calculus_agent/runtime").rglob("*.py")),
    "graders": sorted((ROOT / "tests/evals/graders").glob("*.py")),
    "fixture": [
        *sorted((ROOT / "tests/evals/fixtures").glob("*.py")),
        ROOT / "tests/evals/curriculum_fixture.py",
    ],
    "runner": [ROOT / "tests/evals/runner.py"],
    "model_configuration": [
        ROOT / "src/calculus_agent/config.py",
        ROOT / "src/calculus_agent/runtime/backend.py",
    ],
}


def component_sha(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_holdout_manifest(
    manifest_path: Path,
    dataset_paths: list[Path],
    *,
    verify_current_components: bool = True,
) -> dict[str, Any]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    datasets = load_dataset_cases(dataset_paths)
    selected = manifest.get("cases") or []
    errors: list[str] = []
    selected_ids = [
        str(item.get("case_id")) for item in selected if isinstance(item, dict)
    ]
    holdout_ids = sorted(
        case_id for case_id, case in datasets.items()
        if case.get("split") == "holdout_candidate"
    )
    duplicates = [
        case for case in datasets.values()
        if case.get("split") == "duplicate_regression"
    ]

    if manifest.get("status") != "frozen":
        errors.append("manifest status must be frozen")
    if manifest.get("split") != "holdout_candidate":
        errors.append("manifest split must be holdout_candidate")
    expected_count = manifest.get("expected_case_count")
    if expected_count != len(selected_ids):
        errors.append("expected_case_count does not match manifest cases")
    if len(selected_ids) != len(set(selected_ids)):
        errors.append("manifest contains duplicate case ids")
    if sorted(selected_ids) != holdout_ids:
        errors.append("manifest cases must exactly equal deduplicated Holdout split")

    for case_id in selected_ids:
        case = datasets.get(case_id) or {}
        if (case.get("review") or {}).get("status") != "approved":
            errors.append(f"{case_id}: human review is not approved")
        if case.get("duplicate_of"):
            errors.append(f"{case_id}: duplicate cannot be in frozen Holdout")

    excluded = manifest.get("excluded_duplicates") or {}
    if excluded.get("count") != len(duplicates):
        errors.append("excluded duplicate count does not match dataset")
    if any(not case.get("duplicate_of") for case in duplicates):
        errors.append("every duplicate_regression case must record duplicate_of")

    actual_dataset_sha = dataset_sha(dataset_paths)
    if manifest.get("dataset_sha") != actual_dataset_sha:
        errors.append("manifest dataset_sha does not match dataset")

    execution = manifest.get("execution") or {}
    for field in ("provider", "model", "backend_adapter", "protocol"):
        if not execution.get(field):
            errors.append(f"execution.{field} is required")
    freeze = manifest.get("freeze_lock") or {}
    if freeze.get("human_reviewed") is not True:
        errors.append("freeze_lock.human_reviewed must be true")
    if freeze.get("reviewed_case_count") != len(selected_ids):
        errors.append("freeze_lock.reviewed_case_count is incorrect")
    if re.fullmatch(r"[0-9a-f]{40}", str(freeze.get("code_git_sha") or "")) is None:
        errors.append("freeze_lock.code_git_sha must be a full Git SHA")

    locks = manifest.get("component_locks") or {}
    actual_components = {
        name: component_sha(paths) for name, paths in COMPONENT_PATHS.items()
    }
    if verify_current_components:
        for name, actual_sha in actual_components.items():
            if (locks.get(name) or {}).get("sha256") != actual_sha:
                errors.append(f"component_locks.{name}.sha256 mismatch")

    return {
        "valid": not errors,
        "manifest": str(manifest_path),
        "dataset_sha": actual_dataset_sha,
        "selected_case_count": len(selected_ids),
        "approved_case_count": sum(
            (datasets.get(case_id, {}).get("review") or {}).get("status") == "approved"
            for case_id in selected_ids
        ),
        "excluded_duplicate_count": len(duplicates),
        "component_shas": actual_components,
        "case_ids": selected_ids,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate frozen Holdout manifest")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("datasets", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_holdout_manifest(args.manifest, args.datasets)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

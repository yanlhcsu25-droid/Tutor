"""Fail-closed selection, policy, authorization, and source provenance.

This module deliberately contains no Agent or grader business logic.  It only
controls which already-defined cases may be executed and records what code was
used to execute them.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from evaluation.reliability.case_loader import EvalCase, load_eval_suite

ROOT = Path(__file__).resolve().parents[2]
HOLDOUT_SPLITS = frozenset({"holdout", "holdout_candidate"})
INFRASTRUCTURE_ERROR_CODES = frozenset({
    "provider_rate_limit",
    "provider_connection_error",
    "provider_timeout",
    "provider_http_error",
    "backend_unavailable",
})


class EvaluationControlError(ValueError):
    """A preflight invariant failed; execution must not start."""


@dataclass(frozen=True)
class Selection:
    cases: list[EvalCase]
    dataset_path: Path
    dataset_sha256: str
    manifest_path: Path | None = None
    manifest_sha256: str | None = None
    manifest: dict[str, Any] | None = None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_sources(paths: Iterable[str | Path]) -> dict[str, Any]:
    """Hash a stable bundle of actual source files, including their names."""
    resolved = sorted({Path(path).resolve() for path in paths})
    digest = hashlib.sha256()
    sources: list[dict[str, str]] = []
    for path in resolved:
        if not path.is_file():
            raise EvaluationControlError(f"required source file missing: {path}")
        relative = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        file_hash = sha256_file(path)
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hash))
        sources.append({"path": relative, "sha256": file_hash})
    return {"sha256": digest.hexdigest(), "sources": sources}


def source_provenance() -> dict[str, Any]:
    evaluator_dir = ROOT / "evaluation/reliability/evaluators"
    fixture_dir = ROOT / "evaluation/reliability/fixtures"
    prompt_sources = [
        ROOT / "src/calculus_agent/runtime/coordinator.py",
        ROOT / "src/calculus_agent/agent/semantic_router.py",
        ROOT / "src/calculus_agent/agent/task_router.py",
        ROOT / "src/calculus_agent/agent/skills/loader.py",
        ROOT / "src/calculus_agent/agent/skills/paper_question_operations/SKILL.md",
        ROOT / "src/calculus_agent/agent/skills/teaching_design/SKILL.md",
    ]
    return {
        "runner": sha256_sources([
            ROOT / "evaluation/reliability/runner.py",
            ROOT / "evaluation/reliability/execution_control.py",
            ROOT / "evaluation/reliability/reporting.py",
            ROOT / "evaluation/reliability/langfuse_scores.py",
        ]),
        "graders": sha256_sources(evaluator_dir.glob("*.py")),
        "prompt": sha256_sources(prompt_sources),
        "fixture": sha256_sources([
            *fixture_dir.glob("*.py"),
            ROOT / "evaluation/reliability/curriculum_fixture.py",
            ROOT / "evaluation/reliability/case_loader.py",
            ROOT / "tests/conftest.py",
        ]),
    }


def _load_mapping(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise EvaluationControlError(f"{label} not found: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EvaluationControlError(f"invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationControlError(f"{label} root must be a mapping: {path}")
    return value


def _assert_expected_hash(*, actual: str, expected: str | None, label: str) -> None:
    if expected is not None and actual != expected.lower():
        raise EvaluationControlError(
            f"{label} SHA256 mismatch: expected {expected.lower()}, got {actual}"
        )


def select_cases_fail_closed(
    dataset_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    expected_dataset_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
    case_ids: Iterable[str] = (),
    categories: Iterable[str] = (),
) -> Selection:
    dataset = Path(dataset_path).resolve()
    dataset_hash = sha256_file(dataset)
    _assert_expected_hash(
        actual=dataset_hash, expected=expected_dataset_sha256, label="dataset"
    )
    try:
        suite = load_eval_suite(dataset)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise EvaluationControlError(f"invalid dataset {dataset}: {exc}") from exc
    suite_cases = suite.cases
    by_id = {case.id: case for case in suite_cases}
    if len(by_id) != len(suite_cases):
        duplicates = sorted(
            case_id for case_id in by_id
            if sum(case.id == case_id for case in suite_cases) > 1
        )
        raise EvaluationControlError(f"duplicate dataset case IDs: {duplicates}")

    requested_ids = list(case_ids)
    requested_categories = list(categories)
    if len(requested_ids) != len(set(requested_ids)):
        raise EvaluationControlError("duplicate --case values are not allowed")

    if manifest_path is None:
        unknown = sorted(set(requested_ids) - set(by_id))
        if unknown:
            raise EvaluationControlError(f"unknown requested case IDs: {unknown}")
        selected = [
            case for case in suite_cases
            if (not requested_ids or case.id in requested_ids)
            and (not requested_categories or case.category in requested_categories)
        ]
        if not selected:
            raise EvaluationControlError("case selection is empty")
        return Selection(selected, dataset, dataset_hash)

    if requested_ids or requested_categories:
        raise EvaluationControlError(
            "--case/--category cannot narrow a manifest; manifests are exact case lists"
        )
    manifest_file = Path(manifest_path).resolve()
    manifest_hash = sha256_file(manifest_file)
    _assert_expected_hash(
        actual=manifest_hash, expected=expected_manifest_sha256, label="manifest"
    )
    manifest = _load_mapping(manifest_file, label="manifest")
    raw_entries = manifest.get("cases")
    if not isinstance(raw_entries, list):
        raise EvaluationControlError("manifest cases must be a list")
    manifest_ids = [
        entry.get("case_id") if isinstance(entry, dict) else None for entry in raw_entries
    ]
    if any(not isinstance(case_id, str) or not case_id for case_id in manifest_ids):
        raise EvaluationControlError("every manifest case requires a non-empty case_id")
    if len(manifest_ids) != len(set(manifest_ids)):
        raise EvaluationControlError("manifest contains duplicate case IDs")
    expected_count = manifest.get("expected_case_count", len(manifest_ids))
    if not isinstance(expected_count, int) or expected_count != len(manifest_ids):
        raise EvaluationControlError(
            f"manifest count mismatch: expected {expected_count}, listed {len(manifest_ids)}"
        )
    unknown = sorted(set(manifest_ids) - set(by_id))
    if unknown:
        raise EvaluationControlError(f"manifest references unknown case IDs: {unknown}")

    manifest_dataset_hash = manifest.get("dataset_file_sha256")
    if manifest_dataset_hash is not None:
        if not isinstance(manifest_dataset_hash, str):
            raise EvaluationControlError("manifest dataset_file_sha256 must be a string")
        _assert_expected_hash(
            actual=dataset_hash, expected=manifest_dataset_hash, label="manifest dataset"
        )
    elif expected_dataset_sha256 is None:
        raise EvaluationControlError(
            "manifest lacks dataset_file_sha256; --dataset-sha256 is required"
        )

    selected = [by_id[case_id] for case_id in manifest_ids]
    if len(selected) != expected_count:
        raise EvaluationControlError("selected case count does not match manifest")
    return Selection(
        selected,
        dataset,
        dataset_hash,
        manifest_file,
        manifest_hash,
        manifest,
    )


def enforce_review_policy(
    selection: Selection,
    *,
    mode: str,
    dataset_role: str,
) -> None:
    if mode not in {"diagnostic", "benchmark"}:
        raise EvaluationControlError(f"unknown execution mode: {mode}")
    if dataset_role not in {
        "development", "regression", "holdout", "duplicate-regression"
    }:
        raise EvaluationControlError(f"unknown dataset role: {dataset_role}")

    selected_splits = {str(case.raw.get("split") or "unspecified") for case in selection.cases}
    contains_holdout = bool(selected_splits & HOLDOUT_SPLITS)
    if contains_holdout and dataset_role != "holdout":
        raise EvaluationControlError("holdout cases must use --dataset-role holdout")
    if dataset_role == "holdout":
        if mode != "benchmark":
            raise EvaluationControlError("holdout cases may run only in benchmark mode")
        if selection.manifest is None:
            raise EvaluationControlError("holdout execution requires an exact manifest")
        if not selected_splits.issubset(HOLDOUT_SPLITS):
            raise EvaluationControlError(
                "Holdout manifests cannot mix development or regression cases"
            )
        if str(selection.manifest.get("split")) not in HOLDOUT_SPLITS:
            raise EvaluationControlError(
                "holdout manifest must declare a Holdout split"
            )
        if selection.manifest.get("status") != "frozen":
            raise EvaluationControlError("holdout manifest must have status: frozen")
        freeze_lock = selection.manifest.get("freeze_lock") or {}
        if freeze_lock.get("human_reviewed") is not True:
            raise EvaluationControlError(
                "holdout manifest must have freeze_lock.human_reviewed=true"
            )
    if dataset_role == "duplicate-regression" and mode == "benchmark":
        raise EvaluationControlError(
            "duplicate regression cases are diagnostic and not a formal benchmark"
        )

    blocked: list[str] = []
    unapproved: list[str] = []
    for case in selection.cases:
        review = case.raw.get("review") or {}
        readiness = str(review.get("readiness") or "").upper()
        if readiness == "BLOCKED" or review.get("status") == "blocked":
            blocked.append(case.id)
        if mode == "benchmark" and review.get("status") != "approved":
            unapproved.append(case.id)
    if blocked:
        raise EvaluationControlError(f"blocked cases cannot execute: {blocked}")
    if unapproved:
        raise EvaluationControlError(
            f"benchmark requires review.status=approved: {unapproved}"
        )


def validate_manifest_execution_config(
    selection: Selection,
    *,
    actual: dict[str, Any],
) -> None:
    """Require declared Benchmark runtime settings to match actual settings."""
    if selection.manifest is None:
        return
    declared = selection.manifest.get("execution")
    if not isinstance(declared, dict):
        raise EvaluationControlError("benchmark manifest requires execution config")
    comparisons = {
        "provider": (declared.get("provider"), actual.get("provider")),
        "model": (declared.get("model"), actual.get("model")),
        "backend_adapter": (
            declared.get("backend_adapter"), actual.get("backend_adapter")
        ),
        "protocol": (declared.get("protocol"), actual.get("protocol")),
        "temperature": (declared.get("temperature"), actual.get("temperature")),
        "timeout_seconds": (
            declared.get("timeout_seconds"), actual.get("timeout_seconds")
        ),
        "case_delay_seconds": (
            declared.get("case_delay_seconds"), actual.get("case_delay_seconds")
        ),
    }
    retry_policy = declared.get("retry_policy") or {}
    comparisons.update({
        "retry_policy.provider_retries": (
            retry_policy.get("provider_retries"), actual.get("max_retries")
        ),
        "retry_policy.retry_base_seconds": (
            retry_policy.get("retry_base_seconds"), actual.get("retry_base_seconds")
        ),
    })
    missing = [key for key, (expected, _) in comparisons.items() if expected is None]
    if missing:
        raise EvaluationControlError(
            f"benchmark manifest execution config is incomplete: {missing}"
        )
    mismatches = [
        f"{key}: expected {expected!r}, got {observed!r}"
        for key, (expected, observed) in comparisons.items()
        if expected != observed
    ]
    if mismatches:
        raise EvaluationControlError(
            "manifest execution config mismatch: " + "; ".join(mismatches)
        )


def _parse_expiration(value: Any) -> datetime:
    if not isinstance(value, str):
        raise EvaluationControlError("authorization expires_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvaluationControlError("authorization expires_at is invalid") from exc
    if parsed.tzinfo is None:
        raise EvaluationControlError("authorization expires_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def consume_holdout_authorization(
    authorization_path: str | Path,
    *,
    selection: Selection,
    provenance: dict[str, Any],
    run_id: str,
    consumption_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate and atomically consume one authorization before any model call."""
    if selection.manifest is None or selection.manifest_sha256 is None:
        raise EvaluationControlError("holdout authorization requires a manifest selection")
    path = Path(authorization_path).resolve()
    authorization = _load_mapping(path, label="authorization")
    required = {
        "authorization_id", "dataset_sha256", "manifest_sha256", "expires_at",
        "status", "single_use", "declared_grader_version", "grader_sha256",
        "runner_sha256", "prompt_sha256", "fixture_sha256",
    }
    missing = sorted(required - set(authorization))
    if missing:
        raise EvaluationControlError(f"authorization missing required fields: {missing}")
    if authorization["status"] != "active" or authorization["single_use"] is not True:
        raise EvaluationControlError("authorization must be active and single_use=true")
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if _parse_expiration(authorization["expires_at"]) <= current_time:
        raise EvaluationControlError("authorization has expired")

    expected_values = {
        "dataset_sha256": selection.dataset_sha256,
        "manifest_sha256": selection.manifest_sha256,
        "grader_sha256": provenance["graders"]["sha256"],
        "runner_sha256": provenance["runner"]["sha256"],
        "prompt_sha256": provenance["prompt"]["sha256"],
        "fixture_sha256": provenance["fixture"]["sha256"],
        "declared_grader_version": (selection.manifest.get("versions") or {}).get("grader"),
    }
    for key, expected in expected_values.items():
        if authorization.get(key) != expected:
            raise EvaluationControlError(
                f"authorization {key} mismatch: expected {expected!r}, "
                f"got {authorization.get(key)!r}"
            )

    consumed = (
        Path(consumption_path).resolve()
        if consumption_path is not None
        else path.with_suffix(path.suffix + ".consumed.json")
    )
    receipt = {
        "authorization_id": authorization["authorization_id"],
        "run_id": run_id,
        "consumed_at": current_time.isoformat().replace("+00:00", "Z"),
        "authorization_sha256": sha256_file(path),
        "dataset_sha256": selection.dataset_sha256,
        "manifest_sha256": selection.manifest_sha256,
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(consumed, flags, 0o600)
    except FileExistsError as exc:
        raise EvaluationControlError(
            f"authorization already consumed: {consumed}"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # Never remove the claim: a crash after claiming must remain fail-closed.
        raise
    return {**receipt, "consumption_path": str(consumed)}


def classify_execution_status(
    *,
    passed: bool,
    runtime_error: dict[str, Any] | None = None,
    exception: BaseException | None = None,
    phase: str | None = None,
) -> str:
    if passed:
        return "passed"
    error = runtime_error or {}
    code = str(error.get("code") or "")
    stage = str(error.get("stage") or "")
    error_type = str(error.get("type") or "")
    if (
        code in INFRASTRUCTURE_ERROR_CODES
        or code.startswith("provider_")
        or stage == "backend_initialization"
        or error_type in {"HTTPError", "URLError", "TimeoutError", "ConnectionError"}
        or isinstance(exception, (TimeoutError, ConnectionError))
        or phase == "infrastructure"
    ):
        return "infrastructure_failed"
    if phase == "fixture":
        return "fixture_failed"
    if phase == "runner":
        return "runner_failed"
    return "agent_failed"

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from evaluation.reliability import runner
from evaluation.reliability.case_loader import EvalCase
from evaluation.reliability.execution_control import (
    EvaluationControlError,
    Selection,
    classify_execution_status,
    consume_holdout_authorization,
    enforce_review_policy,
    select_cases_fail_closed,
    sha256_file,
    sha256_sources,
    validate_manifest_execution_config,
)
from evaluation.reliability.reporting import aggregate_reports, summarize_results


def _dataset(tmp_path: Path, cases: list[dict] | None = None) -> Path:
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump({
        "version": "1",
        "suite": {"name": "test"},
        "cases": cases or [
            {"id": "A", "category": "x", "review": {"status": "approved"}},
            {"id": "B", "category": "x", "review": {"status": "approved"}},
        ],
    }), encoding="utf-8")
    return path


def _manifest(tmp_path: Path, dataset: Path, ids=("A", "B"), count=2) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump({
        "name": "test-manifest",
        "status": "frozen",
        "split": "holdout",
        "freeze_lock": {"human_reviewed": True},
        "expected_case_count": count,
        "dataset_file_sha256": sha256_file(dataset),
        "versions": {"grader": "grader-v2"},
        "cases": [{"case_id": case_id} for case_id in ids],
    }), encoding="utf-8")
    return path


def test_manifest_selection_succeeds_and_preserves_order(tmp_path):
    dataset = _dataset(tmp_path)
    manifest = _manifest(tmp_path, dataset, ids=("B", "A"))

    selected = select_cases_fail_closed(
        dataset,
        manifest_path=manifest,
        expected_manifest_sha256=sha256_file(manifest),
    )

    assert [case.id for case in selected.cases] == ["B", "A"]
    assert selected.dataset_sha256 == sha256_file(dataset)


@pytest.mark.parametrize(
    ("ids", "count", "message"),
    [
        (("A", "MISSING"), 2, "unknown case IDs"),
        (("A", "A"), 2, "duplicate case IDs"),
        (("A", "B"), 3, "count mismatch"),
    ],
)
def test_manifest_fails_closed_for_invalid_case_contract(
    tmp_path, ids, count, message
):
    dataset = _dataset(tmp_path)
    manifest = _manifest(tmp_path, dataset, ids=ids, count=count)

    with pytest.raises(EvaluationControlError, match=message):
        select_cases_fail_closed(dataset, manifest_path=manifest)


def test_benchmark_manifest_execution_config_mismatch_fails_closed(tmp_path):
    dataset = _dataset(tmp_path)
    manifest = _manifest(tmp_path, dataset)
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["execution"] = {
        "provider": "siliconflow",
        "model": "expected-model",
        "backend_adapter": "BailianChatBackend",
        "protocol": "openai_compatible",
        "temperature": 0,
        "timeout_seconds": 120,
        "case_delay_seconds": 5,
        "retry_policy": {"provider_retries": 4, "retry_base_seconds": 5.0},
    }
    manifest.write_text(yaml.safe_dump(payload), encoding="utf-8")
    selection = select_cases_fail_closed(dataset, manifest_path=manifest)

    with pytest.raises(EvaluationControlError, match="execution config mismatch"):
        validate_manifest_execution_config(selection, actual={
            "provider": "siliconflow",
            "model": "wrong-model",
            "backend_adapter": "BailianChatBackend",
            "protocol": "openai_compatible",
            "temperature": 0,
            "timeout_seconds": 120,
            "case_delay_seconds": 5,
            "max_retries": 4,
            "retry_base_seconds": 5.0,
        })


def test_manifest_fails_closed_for_dataset_or_manifest_hash_mismatch(tmp_path):
    dataset = _dataset(tmp_path)
    manifest = _manifest(tmp_path, dataset)

    with pytest.raises(EvaluationControlError, match="dataset SHA256 mismatch"):
        select_cases_fail_closed(dataset, expected_dataset_sha256="0" * 64)
    with pytest.raises(EvaluationControlError, match="manifest SHA256 mismatch"):
        select_cases_fail_closed(
            dataset, manifest_path=manifest, expected_manifest_sha256="0" * 64
        )


def _selection_for_review(status: str, *, readiness: str | None = None) -> Selection:
    review = {"status": status}
    if readiness:
        review["readiness"] = readiness
    case = EvalCase(id="A", category="x", title="A", raw={"split": "dev", "review": review})
    return Selection([case], Path("cases.yaml"), "d" * 64)


def test_diagnostic_allows_pending_but_benchmark_rejects_it():
    selection = _selection_for_review("pending")
    enforce_review_policy(selection, mode="diagnostic", dataset_role="development")

    with pytest.raises(EvaluationControlError, match="requires review.status=approved"):
        enforce_review_policy(selection, mode="benchmark", dataset_role="development")


def test_blocked_case_is_rejected_even_in_diagnostic_mode():
    selection = _selection_for_review("pending", readiness="BLOCKED")
    with pytest.raises(EvaluationControlError, match="blocked cases"):
        enforce_review_policy(selection, mode="diagnostic", dataset_role="development")


def _holdout_selection(tmp_path: Path) -> Selection:
    dataset = _dataset(tmp_path, [{
        "id": "H", "category": "x", "split": "holdout_candidate",
        "review": {"status": "approved"},
    }])
    manifest = _manifest(tmp_path, dataset, ids=("H",), count=1)
    return select_cases_fail_closed(dataset, manifest_path=manifest)


def _provenance() -> dict:
    return {
        name: {"sha256": character * 64, "sources": []}
        for name, character in zip(
            ("runner", "graders", "prompt", "fixture"), "rgpf", strict=True
        )
    }


def _authorization(
    path: Path,
    selection: Selection,
    provenance: dict,
    *,
    expires_at: datetime,
) -> None:
    path.write_text(yaml.safe_dump({
        "authorization_id": "auth-1",
        "dataset_sha256": selection.dataset_sha256,
        "manifest_sha256": selection.manifest_sha256,
        "expires_at": expires_at.isoformat(),
        "status": "active",
        "single_use": True,
        "declared_grader_version": "grader-v2",
        "grader_sha256": provenance["graders"]["sha256"],
        "runner_sha256": provenance["runner"]["sha256"],
        "prompt_sha256": provenance["prompt"]["sha256"],
        "fixture_sha256": provenance["fixture"]["sha256"],
    }), encoding="utf-8")


def test_holdout_without_authorization_is_rejected_before_execution(
    tmp_path, monkeypatch, capsys
):
    selection = _holdout_selection(tmp_path)
    args = SimpleNamespace(
        cases_file=selection.dataset_path,
        case_ids=None,
        categories=None,
        report=tmp_path / "report.json",
        variant="state-policy",
        case_delay_seconds=0,
        mode="benchmark",
        dataset_role="holdout",
        run_id="run-no-auth",
        manifest=selection.manifest_path,
        manifest_sha256=None,
        dataset_sha256=None,
        authorization_file=None,
        authorization_consumption_file=None,
        expected_grader_sha256=None,
    )
    monkeypatch.setattr(runner, "parse_args", lambda: args)
    monkeypatch.setattr(runner, "select_cases_fail_closed", lambda *a, **k: selection)
    monkeypatch.setattr(runner, "source_provenance", _provenance)

    assert runner.main() == 2
    assert "requires --authorization-file" in capsys.readouterr().err


def test_expired_authorization_is_rejected(tmp_path):
    selection = _holdout_selection(tmp_path)
    provenance = _provenance()
    authorization = tmp_path / "authorization.yaml"
    now = datetime.now(timezone.utc)
    _authorization(
        authorization, selection, provenance, expires_at=now - timedelta(seconds=1)
    )

    with pytest.raises(EvaluationControlError, match="expired"):
        consume_holdout_authorization(
            authorization,
            selection=selection,
            provenance=provenance,
            run_id="run-1",
            now=now,
        )


def test_authorization_component_hash_mismatch_is_rejected(tmp_path):
    selection = _holdout_selection(tmp_path)
    provenance = _provenance()
    authorization = tmp_path / "authorization.yaml"
    now = datetime.now(timezone.utc)
    _authorization(
        authorization, selection, provenance, expires_at=now + timedelta(hours=1)
    )
    changed = {**provenance, "graders": {"sha256": "x" * 64, "sources": []}}

    with pytest.raises(EvaluationControlError, match="grader_sha256 mismatch"):
        consume_holdout_authorization(
            authorization,
            selection=selection,
            provenance=changed,
            run_id="run-1",
            now=now,
        )


def test_authorization_cannot_be_reused(tmp_path):
    selection = _holdout_selection(tmp_path)
    provenance = _provenance()
    authorization = tmp_path / "authorization.yaml"
    now = datetime.now(timezone.utc)
    _authorization(
        authorization, selection, provenance, expires_at=now + timedelta(hours=1)
    )
    consume_holdout_authorization(
        authorization, selection=selection, provenance=provenance, run_id="run-1", now=now
    )

    with pytest.raises(EvaluationControlError, match="already consumed"):
        consume_holdout_authorization(
            authorization,
            selection=selection,
            provenance=provenance,
            run_id="run-2",
            now=now,
        )


def test_authorization_consumption_is_atomic_under_concurrency(tmp_path):
    selection = _holdout_selection(tmp_path)
    provenance = _provenance()
    authorization = tmp_path / "authorization.yaml"
    now = datetime.now(timezone.utc)
    _authorization(
        authorization, selection, provenance, expires_at=now + timedelta(hours=1)
    )

    def consume(index: int) -> str:
        try:
            consume_holdout_authorization(
                authorization,
                selection=selection,
                provenance=provenance,
                run_id=f"run-{index}",
                now=now,
            )
            return "consumed"
        except EvaluationControlError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(consume, range(8)))
    assert outcomes.count("consumed") == 1
    assert outcomes.count("rejected") == 7


def test_provider_429_is_excluded_from_agent_denominator():
    provider_status = classify_execution_status(
        passed=False,
        runtime_error={"code": "provider_rate_limit", "stage": "llm_call"},
    )
    summary = summarize_results([
        {"case_id": "pass", "passed": True, "execution_status": "passed", "graders": []},
        {
            "case_id": "agent", "passed": False,
            "execution_status": "agent_failed", "graders": [],
        },
        {
            "case_id": "infra", "passed": False,
            "execution_status": provider_status, "graders": [],
        },
    ], mode="benchmark")

    assert summary["agent_denominator"] == 2
    assert summary["agent_pass_rate"] == 0.5
    assert summary["execution_status"]["infrastructure_failed"] == 1
    assert summary["benchmark_complete"] is False


@pytest.mark.parametrize(
    ("phase", "expected"),
    [("fixture", "fixture_failed"), ("runner", "runner_failed")],
)
def test_non_agent_exceptions_have_separate_statuses(phase, expected):
    assert classify_execution_status(
        passed=False, exception=RuntimeError("boom"), phase=phase
    ) == expected


def test_partial_benchmark_report_is_not_complete():
    summary = summarize_results(
        [{"case_id": "A", "passed": True, "execution_status": "passed", "graders": []}],
        mode="benchmark",
        expected_cases=2,
    )
    assert summary["benchmark_complete"] is False
    assert summary["completed_cases"] == 1
    assert summary["expected_cases"] == 2


def test_skipped_and_not_applicable_graders_are_not_counted_as_passes():
    summary = summarize_results([{
        "case_id": "A",
        "passed": False,
        "execution_status": "agent_failed",
        "graders": [
            {"grader": "state", "skipped": True, "passed": True},
            {"grader": "clarification_contract", "eligible": False, "passed": True},
        ],
    }])

    assert summary["graders"]["state"]["passed"] == 0
    assert summary["graders"]["state"]["skipped"] == 1
    clarification = summary["graders"]["clarification_contract"]
    assert clarification["not_applicable"] == 1
    assert clarification["pass_rate"] is None


def test_source_bundle_hash_changes_when_source_changes(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("x = 1\n", encoding="utf-8")
    first = sha256_sources([source])["sha256"]
    source.write_text("x = 2\n", encoding="utf-8")
    assert sha256_sources([source])["sha256"] != first


def _report(path: Path, *, run_id: str, dataset_sha: str, case_id: str, retries=4):
    execution = {
        "mode": "diagnostic",
        "dataset_role": "development",
        "variant": "state-policy",
        "model_ids": ["model"],
        "providers": ["siliconflow"],
        "backend_adapters": ["BailianChatBackend"],
        "protocols": ["openai_compatible"],
        "temperatures": [0],
        "timeout_seconds": 120,
        "max_retries": retries,
        "retry_base_seconds": 5,
        "case_delay_seconds": 5,
        "langfuse_enabled": False,
    }
    path.write_text(json.dumps({
        "metadata": {
            "git_sha": "test-git-sha",
            "git_dirty": False,
            "run_id": run_id,
            "dataset": {
                "path": f"{dataset_sha}.yaml",
                "sha256": dataset_sha,
                "case_count": 1,
            },
            "execution": execution,
            "reproducibility": {
                name: {"sha256": name * 8} for name in (
                    "runner", "graders", "prompt", "fixture"
                )
            },
        },
        "results": [{
            "case_id": case_id, "passed": True,
            "execution_status": "passed", "graders": [],
        }],
    }), encoding="utf-8")


def test_compatible_reports_aggregate_and_incompatible_reports_are_rejected(tmp_path):
    first = tmp_path / "dev.json"
    second = tmp_path / "devx.json"
    _report(first, run_id="dev", dataset_sha="a" * 64, case_id="A")
    _report(second, run_id="devx", dataset_sha="b" * 64, case_id="B")

    aggregate = aggregate_reports([first, second])
    assert aggregate["summary"]["cases"] == 2
    assert len(aggregate["metadata"]["source_reports"]) == 2

    _report(second, run_id="devx", dataset_sha="b" * 64, case_id="B", retries=5)
    with pytest.raises(EvaluationControlError, match="incompatible"):
        aggregate_reports([first, second])

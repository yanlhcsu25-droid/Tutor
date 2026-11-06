from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from evaluation.reliability import execution_control, runner
from evaluation.reliability.case_loader import EvalCase
from evaluation.reliability.execution_control import (
    EvaluationControlError,
    Selection,
    classify_execution_status,
    clean_git_sha,
    component_source_paths,
    consume_holdout_authorization,
    enforce_review_policy,
    git_commit_is_ancestor,
    select_cases_fail_closed,
    sha256_file,
    sha256_sources,
    source_provenance,
    validate_manifest_component_locks,
    validate_manifest_execution_config,
)
from evaluation.reliability.reporting import (
    EXECUTION_STATUSES,
    aggregate_reports,
    summarize_results,
)


@pytest.fixture(autouse=True)
def _isolated_holdout_consumption_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        execution_control,
        "HOLDOUT_CONSUMPTION_DIR",
        tmp_path / "holdout-consumptions",
    )


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


def test_non_frozen_manifest_is_rejected_for_benchmark(tmp_path):
    dataset = _dataset(tmp_path)
    manifest_path = _manifest(tmp_path, dataset)
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "draft"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    selection = select_cases_fail_closed(dataset, manifest_path=manifest_path)

    with pytest.raises(EvaluationControlError, match="status: frozen"):
        enforce_review_policy(selection, mode="benchmark", dataset_role="development")


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


def test_diagnostic_still_allows_flexible_case_selection(tmp_path):
    dataset = _dataset(tmp_path)
    selection = select_cases_fail_closed(dataset, case_ids=["B"])
    enforce_review_policy(selection, mode="diagnostic", dataset_role="development")
    assert [case.id for case in selection.cases] == ["B"]


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


def test_diagnostic_allows_pending_but_benchmark_requires_frozen_manifest():
    selection = _selection_for_review("pending")
    enforce_review_policy(selection, mode="diagnostic", dataset_role="development")

    with pytest.raises(EvaluationControlError, match="requires a frozen manifest"):
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
    names = (
        "runtime", "agent", "tools", "services", "prompt", "graders",
        "fixture", "runner", "model_configuration",
    )
    return {
        name: {"sha256": f"{index:064x}", "sources": []}
        for index, name in enumerate(names, start=1)
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
        "runtime_sha256": provenance["runtime"]["sha256"],
        "agent_sha256": provenance["agent"]["sha256"],
        "tools_sha256": provenance["tools"]["sha256"],
        "services_sha256": provenance["services"]["sha256"],
        "prompt_sha256": provenance["prompt"]["sha256"],
        "grader_sha256": provenance["graders"]["sha256"],
        "fixture_sha256": provenance["fixture"]["sha256"],
        "runner_sha256": provenance["runner"]["sha256"],
        "model_configuration_sha256": provenance["model_configuration"]["sha256"],
    }), encoding="utf-8")


def test_real_backend_requires_explicit_live_llm_opt_in(monkeypatch):
    case = EvalCase(id="live", category="x", title="live")
    build_calls = []
    monkeypatch.delenv("RUN_LIVE_LLM", raising=False)
    monkeypatch.setattr(
        runner,
        "build_teacher_agent_backend",
        lambda settings: build_calls.append(settings),
    )

    with pytest.raises(EvaluationControlError, match="RUN_LIVE_LLM=1"):
        runner.create_eval_backend(case)
    assert build_calls == []


def test_real_backend_is_created_after_live_llm_opt_in(monkeypatch):
    case = EvalCase(id="live", category="x", title="live")
    backend = object()
    monkeypatch.setenv("RUN_LIVE_LLM", "1")
    monkeypatch.setattr(runner, "build_teacher_agent_backend", lambda settings: backend)
    assert runner.create_eval_backend(case) is backend


def test_scripted_backend_does_not_require_live_llm_opt_in(monkeypatch):
    monkeypatch.delenv("RUN_LIVE_LLM", raising=False)
    case = EvalCase(
        id="scripted",
        category="x",
        title="scripted",
        backend={"behavior": "scripted", "responses": []},
    )
    backend = runner.create_eval_backend(case)
    assert backend.model == "eval-scripted-backend"


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


def test_copied_authorization_cannot_reuse_same_authorization_id(tmp_path):
    selection = _holdout_selection(tmp_path)
    provenance = _provenance()
    authorization = tmp_path / "authorization.yaml"
    copied = tmp_path / "copied" / "renamed.yaml"
    now = datetime.now(timezone.utc)
    _authorization(
        authorization, selection, provenance, expires_at=now + timedelta(hours=1)
    )
    copied.parent.mkdir()
    copied.write_bytes(authorization.read_bytes())

    consume_holdout_authorization(
        authorization, selection=selection, provenance=provenance, run_id="run-1", now=now
    )
    with pytest.raises(EvaluationControlError, match="already consumed"):
        consume_holdout_authorization(
            copied, selection=selection, provenance=provenance, run_id="run-2", now=now
        )


def test_authorization_write_failure_remains_consumed(tmp_path, monkeypatch):
    selection = _holdout_selection(tmp_path)
    provenance = _provenance()
    authorization = tmp_path / "authorization.yaml"
    now = datetime.now(timezone.utc)
    _authorization(
        authorization, selection, provenance, expires_at=now + timedelta(hours=1)
    )

    real_dump = execution_control.json.dump
    monkeypatch.setattr(
        execution_control.json,
        "dump",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk failure")),
    )
    with pytest.raises(OSError, match="disk failure"):
        consume_holdout_authorization(
            authorization,
            selection=selection,
            provenance=provenance,
            run_id="run-1",
            now=now,
        )

    monkeypatch.setattr(execution_control.json, "dump", real_dump)
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


def test_runner_case_return_paths_always_have_known_execution_status(
    monkeypatch,
):
    cases = [
        EvalCase(
            id="status-pass",
            category="test",
            title="pass",
            turns=[{"user": "hello"}],
            backend={
                "behavior": "scripted",
                "responses": [{"role": "assistant", "content": "ok"}],
            },
        ),
        EvalCase(
            id="status-fixture",
            category="test",
            title="fixture failure",
            turns=[],
            backend={"behavior": "scripted"},
        ),
        EvalCase(
            id="status-agent",
            category="test",
            title="agent failure",
            turns=[{"user": "hello"}],
            graders=[{"type": "unknown"}],
            backend={
                "behavior": "scripted",
                "responses": [{"role": "assistant", "content": "ok"}],
            },
        ),
    ]
    monkeypatch.delenv("RUN_LIVE_LLM", raising=False)
    results = [runner.run_case(case) for case in cases]
    results.append(runner.run_case(EvalCase(
        id="status-infrastructure",
        category="test",
        title="live backend denied",
        turns=[{"user": "hello"}],
    )))

    original_run_graders = runner.run_graders
    monkeypatch.setattr(
        runner,
        "run_graders",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("runner bug")),
    )
    results.append(runner.run_case(EvalCase(
        id="status-runner",
        category="test",
        title="runner failure",
        turns=[{"user": "hello"}],
        backend={
            "behavior": "scripted",
            "responses": [{"role": "assistant", "content": "ok"}],
        },
    )))
    monkeypatch.setattr(runner, "run_graders", original_run_graders)

    assert {result["execution_status"] for result in results} <= set(
        EXECUTION_STATUSES
    )
    assert {result["execution_status"] for result in results} == {
        "passed",
        "agent_failed",
        "infrastructure_failed",
        "fixture_failed",
        "runner_failed",
    }


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


def _write_minimal_source_tree(root: Path) -> None:
    paths = (
        "src/calculus_agent/config.py",
        "src/calculus_agent/runtime/backend.py",
        "src/calculus_agent/runtime/runtime.py",
        "src/calculus_agent/agent/agent.py",
        "src/calculus_agent/agent/prompts.py",
        "src/calculus_agent/agent/paper_change_service.py",
        "src/calculus_agent/agent/paper_tool_registry.py",
        "src/calculus_agent/agent/tool_registry.py",
        "src/calculus_agent/agent/toolkit.py",
        "src/calculus_agent/agent/tools/nested/tool.py",
        "src/calculus_agent/agent/tool_adapters/nested/adapter.py",
        "src/calculus_agent/agent/services/nested/service.py",
        "src/calculus_agent/agent/state/nested/state.py",
        "src/calculus_agent/agent/skills/loader.py",
        "src/calculus_agent/agent/skills/nested/SKILL.md",
        "src/calculus_agent/papers/service.py",
        "evaluation/reliability/evaluators/nested/grader.py",
        "evaluation/reliability/fixtures/nested/fixture.py",
        "evaluation/reliability/curriculum_fixture.py",
        "evaluation/reliability/case_loader.py",
        "evaluation/reliability/runner.py",
        "evaluation/reliability/execution_control.py",
        "evaluation/reliability/reporting.py",
        "evaluation/reliability/langfuse_scores.py",
        "tests/conftest.py",
    )
    for relative in paths:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {relative}\n", encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=root, text=True, stderr=subprocess.STDOUT
    ).strip()


def _commit_all(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(
        root,
        "-c", "user.name=Evaluation Test",
        "-c", "user.email=evaluation@example.invalid",
        "commit", "-m", message,
    )
    return _git(root, "rev-parse", "HEAD")


def test_runtime_agent_and_grader_component_hashes_track_source_changes(tmp_path):
    sources = {
        "runtime": tmp_path / "src/calculus_agent/runtime/runtime.py",
        "agent": tmp_path / "src/calculus_agent/agent/agent.py",
        "graders": tmp_path / "evaluation/reliability/evaluators/state.py",
    }
    for source in sources.values():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("x = 1\n", encoding="utf-8")

    paths = component_source_paths(tmp_path)
    before = {
        name: sha256_sources(paths[name], root=tmp_path)["sha256"]
        for name in sources
    }
    for name, source in sources.items():
        source.write_text("x = 2\n", encoding="utf-8")
        assert (
            sha256_sources(
                component_source_paths(tmp_path)[name], root=tmp_path
            )["sha256"]
            != before[name]
        )


@pytest.mark.parametrize(
    ("component", "relative_path"),
    [
        ("agent", "src/calculus_agent/agent/nested/decision.py"),
        ("tools", "src/calculus_agent/agent/tools/nested/tool.py"),
        ("services", "src/calculus_agent/agent/services/nested/service.py"),
        ("prompt", "src/calculus_agent/agent/skills/nested/SKILL.md"),
        ("fixture", "evaluation/reliability/fixtures/nested/fixture.py"),
        ("graders", "evaluation/reliability/evaluators/nested/grader.py"),
        ("runner", "evaluation/reliability/runner.py"),
    ],
)
def test_recursive_component_hashes_track_nested_sources(
    tmp_path, component, relative_path
):
    _write_minimal_source_tree(tmp_path)
    source = tmp_path / relative_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("x = 1\n", encoding="utf-8")
    before = source_provenance(tmp_path)[component]["sha256"]

    source.write_text("x = 2\n", encoding="utf-8")

    assert source_provenance(tmp_path)[component]["sha256"] != before


def test_component_inventory_covers_agent_and_reliability_subtrees():
    paths = component_source_paths()
    covered_agent = set().union(*(
        set(paths[name]) for name in ("agent", "tools", "services", "prompt")
    ))
    agent_root = Path("src/calculus_agent/agent").resolve()
    expected_agent = set(agent_root.rglob("*.py")) | {
        path for path in (agent_root / "skills").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert expected_agent <= covered_agent

    reliability = Path("evaluation/reliability").resolve()
    assert set((reliability / "evaluators").rglob("*.py")) == set(paths["graders"])
    assert set((reliability / "fixtures").rglob("*.py")) <= set(paths["fixture"])
    expected_runner = {
        path for path in reliability.rglob("*.py")
        if not set(path.relative_to(reliability).parts) & {
            "datasets", "evaluators", "fixtures"
        }
    }
    assert expected_runner == set(paths["runner"])


def test_manifest_component_mismatch_names_the_component(tmp_path):
    dataset = _dataset(tmp_path)
    manifest_path = _manifest(tmp_path, dataset)
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    provenance = _provenance()
    manifest["freeze_lock"]["code_git_sha"] = "a" * 40
    manifest["component_locks"] = {
        name: {"sha256": value["sha256"]}
        for name, value in provenance.items()
    }
    manifest["component_locks"]["runtime"]["sha256"] = "bad"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    selection = select_cases_fail_closed(dataset, manifest_path=manifest_path)

    with pytest.raises(EvaluationControlError, match="runtime"):
        validate_manifest_component_locks(
            selection,
            provenance=provenance,
            git_sha="a" * 40,
            grader_version="grader-v2",
        )


def test_manifest_grader_version_mismatch_is_rejected(tmp_path):
    dataset = _dataset(tmp_path)
    manifest_path = _manifest(tmp_path, dataset)
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    provenance = _provenance()
    manifest["freeze_lock"]["code_git_sha"] = "a" * 40
    manifest["component_locks"] = {
        name: {"sha256": value["sha256"]}
        for name, value in provenance.items()
    }
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    selection = select_cases_fail_closed(dataset, manifest_path=manifest_path)

    with pytest.raises(EvaluationControlError, match="grader version mismatch"):
        validate_manifest_component_locks(
            selection,
            provenance=provenance,
            git_sha="a" * 40,
            grader_version="grader-v1",
        )


def test_frozen_manifest_commit_can_follow_source_commit(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _write_minimal_source_tree(repository)
    dataset = _dataset(repository, [{
        "id": "A",
        "category": "x",
        "split": "development",
        "review": {"status": "approved"},
    }])
    source_git_sha = _commit_all(repository, "freeze benchmark source")
    provenance = source_provenance(repository)
    execution = {
        "provider": "siliconflow",
        "model": "test-model",
        "backend_adapter": "BailianChatBackend",
        "protocol": "openai_compatible",
        "temperature": 0,
        "timeout_seconds": 120,
        "case_delay_seconds": 5,
        "retry_policy": {
            "provider_retries": 4,
            "retry_base_seconds": 5.0,
        },
    }
    manifest = repository / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({
        "name": "frozen-development-benchmark",
        "status": "frozen",
        "split": "development",
        "expected_case_count": 1,
        "dataset_file_sha256": sha256_file(dataset),
        "versions": {"grader": "grader-v1"},
        "freeze_lock": {
            "human_reviewed": True,
            "code_git_sha": source_git_sha,
        },
        "component_locks": {
            name: {"sha256": value["sha256"]}
            for name, value in provenance.items()
        },
        "execution": execution,
        "cases": [{"case_id": "A"}],
    }), encoding="utf-8")
    manifest_commit = _commit_all(repository, "commit frozen manifest")

    current_git_sha = clean_git_sha(repository)
    assert current_git_sha == manifest_commit
    assert current_git_sha != source_git_sha
    assert git_commit_is_ancestor(
        source_git_sha, current_git_sha, root=repository
    )

    selection = select_cases_fail_closed(
        dataset,
        manifest_path=manifest,
        expected_dataset_sha256=sha256_file(dataset),
        expected_manifest_sha256=sha256_file(manifest),
    )
    enforce_review_policy(
        selection, mode="benchmark", dataset_role="development"
    )
    validate_manifest_component_locks(
        selection,
        provenance=source_provenance(repository),
        git_sha=current_git_sha,
        grader_version="grader-v1",
        root=repository,
    )
    validate_manifest_execution_config(selection, actual={
        "provider": "siliconflow",
        "model": "test-model",
        "backend_adapter": "BailianChatBackend",
        "protocol": "openai_compatible",
        "temperature": 0,
        "timeout_seconds": 120,
        "case_delay_seconds": 5,
        "max_retries": 4,
        "retry_base_seconds": 5.0,
    })


def test_non_ancestor_source_commit_is_rejected(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    source = repository / "source.py"
    source.write_text("first\n", encoding="utf-8")
    frozen_git_sha = _commit_all(repository, "first history")
    _git(repository, "checkout", "--orphan", "unrelated")
    _git(repository, "rm", "-rf", ".")
    source.write_text("unrelated\n", encoding="utf-8")
    current_git_sha = _commit_all(repository, "unrelated history")

    assert not git_commit_is_ancestor(
        frozen_git_sha, current_git_sha, root=repository
    )


def test_dirty_or_untracked_worktree_rejects_formal_benchmark(monkeypatch):
    def fake_check_output(command, **kwargs):
        if command[1:3] == ["rev-parse", "HEAD"]:
            return "a" * 40 + "\n"
        return " M src/calculus_agent/runtime/runtime.py\n?? untracked.txt\n"

    monkeypatch.setattr(execution_control.subprocess, "check_output", fake_check_output)
    with pytest.raises(EvaluationControlError, match="clean Git worktree") as error:
        clean_git_sha()
    assert "untracked.txt" in str(error.value)


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
                    "runtime", "agent", "tools", "services", "prompt", "graders",
                    "fixture", "runner", "model_configuration",
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

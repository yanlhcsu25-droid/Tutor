"""Candidate scenarios: fixture validation offline, explicit opt-in for live runs."""

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone

import pytest

from tests.evals.case_loader import load_eval_suite
from tests.evals.curriculum_fixture import seed_eval_curriculum
from tests.evals.fixtures.paper import seed_success_question_bank
from tests.evals.runner import apply_case_setup, create_eval_session, run_case

CASE_FILE = Path(__file__).parent / "cases/teacher_workflow_candidate_v0.yaml"
SUITE = load_eval_suite(CASE_FILE)


def test_candidate_manifest():
    assert len(SUITE.cases) == 60
    assert len({c.turns[0]["user"] for c in SUITE.cases}) == 60
    assert Counter(c.category for c in SUITE.cases) == {
        "generation": 12, "pending": 10, "paper": 10, "teaching": 10,
        "shortage": 4, "missing_target": 4, "ambiguity": 5, "negation": 5,
    }
    for case in SUITE.cases:
        assert not case.backend  # No scripted backend masquerading as live evaluation.
        acceptance = next(g for g in case.graders if g["type"] == "acceptance")
        assert set(acceptance["statuses"]) <= {
            "completed", "waiting_confirmation", "needs_clarification",
        }
        assert case.raw["review"]["status"] == "pending"
        assert any(g["type"] == "workflow_safety" for g in case.graders)
        assert not set(acceptance["required_tools"]) & set(acceptance["forbidden_tools"])
        assert acceptance["require_trace"]


@pytest.mark.parametrize("case", SUITE.cases, ids=lambda c: c.id)
def test_candidate_fixture_can_be_constructed(case):
    session = create_eval_session()
    try:
        seed_eval_curriculum(session)
        seed_success_question_bank(session)
        apply_case_setup(session=session, conversation_id=f"preflight-{case.id}", case=case)
        session.flush()
    finally:
        session.close()


@pytest.mark.skipif(
    os.getenv("RUN_WORKFLOW_CANDIDATE") != "1" or os.getenv("RUN_LIVE_LLM") != "1",
    reason="requires RUN_WORKFLOW_CANDIDATE=1 and RUN_LIVE_LLM=1 (paid calls)",
)
def test_live_workflow_candidate():
    repeats = int(os.getenv("WORKFLOW_REPEATS", "1"))
    assert 1 <= repeats <= 3
    selected = set(filter(None, os.getenv("WORKFLOW_CASE_IDS", "").split(",")))
    assert selected <= {c.id for c in SUITE.cases}, "unknown case ID"
    cases = [c for c in SUITE.cases if not selected or c.id in selected]
    sha = hashlib.sha256(CASE_FILE.read_bytes()).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = Path(__file__).parent / "reports" / f"workflow-candidate-{stamp}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], text=True))
    failures = []
    with output.open("x", encoding="utf-8") as stream:
        for repeat in range(1, repeats + 1):
            for case in cases:
                start = time.perf_counter()
                try:
                    result = run_case(case)
                except Exception as exc:
                    result = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
                row = {
                    "case_id": case.id, "repeat": repeat,
                    "dataset_sha256": sha, "git_sha": revision, "git_dirty": dirty,
                    "split": "candidate", "elapsed_s": time.perf_counter() - start,
                    "automated_checks_passed": bool(result.get("passed")),
                    "task_success": None,
                    "manual_review": case.raw["review"],
                    "result": result,
                }
                stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                stream.flush()  # Keep completed results even if a later call is interrupted.
                if not result.get("passed"):
                    failures.append(f"{case.id}/repeat-{repeat}")
    assert not failures, f"{failures}; report={output}"

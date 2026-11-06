import json
from pathlib import Path
from types import SimpleNamespace

from evaluation.reliability import langfuse_scores
from evaluation.reliability import runner
from evaluation.reliability.case_loader import EvalCase, EvalSuite


class FailFirstScoreClient:
    def __init__(self):
        self.calls = []
        self.flush_calls = 0

    def create_score(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("first case score failed")

    def flush(self):
        self.flush_calls += 1


def test_suite_publishes_once_per_case_and_flushes_once(tmp_path, monkeypatch):
    cases = [
        EvalCase(id="multi-turn", category="test", title="multi"),
        EvalCase(id="next-case", category="test", title="next"),
    ]
    suite = EvalSuite(
        version="1",
        name="score-runner-test",
        description="",
        defaults={},
        cases=cases,
    )
    report_path = tmp_path / "report.json"
    args = SimpleNamespace(
        cases_file=Path("score-runner-test.yaml"),
        case_ids=None,
        categories=None,
        report=report_path,
        variant="state-policy",
        case_delay_seconds=0,
    )
    local_results = {
        "multi-turn": {
            "case_id": "multi-turn",
            "title": "multi",
            "conversation_id": "session-multi",
            "variant": "state-policy",
            "grader_version": "grader-v1",
            "passed": True,
            "total_turns": 3,
            "graders": [{"grader": "outcome_integrity", "passed": True}],
        },
        "next-case": {
            "case_id": "next-case",
            "title": "next",
            "conversation_id": "session-next",
            "variant": "state-policy",
            "grader_version": "grader-v1",
            "passed": True,
            "total_turns": 1,
            "graders": [{"grader": "outcome_integrity", "passed": True}],
        },
    }
    client = FailFirstScoreClient()

    monkeypatch.setattr(runner, "parse_args", lambda: args)
    monkeypatch.setattr(runner, "load_eval_suite", lambda _: suite)
    monkeypatch.setattr(
        runner,
        "run_case",
        lambda case, *, variant: local_results[case.id],
    )
    monkeypatch.setattr(langfuse_scores, "safe_get_client", lambda: client)

    assert runner.main() == 0

    assert client.flush_calls == 1
    assert [call["session_id"] for call in client.calls].count("session-multi") == 2
    assert [call["session_id"] for call in client.calls].count("session-next") == 2
    assert [call["name"] for call in client.calls if call["session_id"] == "session-multi"] == [
        "tutor_eval_case_passed",
        "tutor_eval_outcome_integrity",
    ]

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"] == {"cases": 2, "passed": 2, "failed": 0, "pass_rate": 1.0}
    assert report["results"][0]["passed"] is True
    assert report["results"][0]["graders"] == local_results["multi-turn"]["graders"]
    assert "langfuse_scores" not in report["results"][0]

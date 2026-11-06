from copy import deepcopy

from calculus_agent.agent import langfuse_tracing
from evaluation.reliability import langfuse_scores


class FakeClient:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail
        self.flush_calls = 0

    def create_score(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("score upload failed")

    def flush(self):
        self.flush_calls += 1


def _result():
    return {
        "case_id": "EVAL-001",
        "conversation_id": "eval-session-001",
        "variant": "state-policy",
        "grader_version": "grader-v1",
        "passed": True,
        "execution_status": "passed",
        "graders": [
            {"grader": "outcome_integrity", "passed": True, "errors": []},
            {"grader": "constraint_preservation", "passed": False, "errors": ["x"]},
        ],
    }


def test_publishes_case_and_boolean_grader_scores(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(langfuse_scores, "safe_get_client", lambda: client)

    langfuse_scores.publish_case_scores(_result())

    assert [(call["name"], call["value"]) for call in client.calls] == [
        ("tutor_eval_case_passed", 1.0),
        ("tutor_eval_outcome_integrity", 1.0),
        ("tutor_eval_constraint_preservation", 0.0),
    ]
    assert all(call["session_id"] == "eval-session-001" for call in client.calls)
    assert all(call["data_type"] == "BOOLEAN" for call in client.calls)
    assert client.calls[1]["metadata"] == {
        "case_id": "EVAL-001",
        "grader_name": "outcome_integrity",
        "variant": "state-policy",
        "grader_version": "grader-v1",
    }


def test_skips_skipped_ineligible_and_invalid_grader_results(monkeypatch, caplog):
    client = FakeClient()
    monkeypatch.setattr(langfuse_scores, "safe_get_client", lambda: client)
    result = _result()
    result["graders"] = [
        {"grader": "state", "passed": True, "skipped": True},
        {"grader": "clarification_contract", "passed": True, "eligible": False},
        {"grader": "missing_passed"},
        {"grader": "non_boolean", "passed": 0},
    ]

    langfuse_scores.publish_case_scores(result)

    assert [call["name"] for call in client.calls] == ["tutor_eval_case_passed"]
    assert "passed must be boolean" in caplog.text


def test_infrastructure_failure_does_not_publish_agent_or_grader_scores(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(langfuse_scores, "safe_get_client", lambda: client)
    result = _result()
    result.update({"passed": False, "execution_status": "infrastructure_failed"})

    langfuse_scores.publish_case_scores(result)

    assert client.calls == []


def test_agent_failure_publishes_false_case_score(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(langfuse_scores, "safe_get_client", lambda: client)
    result = _result()
    result.update({"passed": False, "execution_status": "agent_failed", "graders": []})

    langfuse_scores.publish_case_scores(result)

    assert [(call["name"], call["value"]) for call in client.calls] == [
        ("tutor_eval_case_passed", 0.0)
    ]


def test_case_score_requires_boolean_passed(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(langfuse_scores, "safe_get_client", lambda: client)
    result = _result()
    result["passed"] = None
    result["graders"] = []

    langfuse_scores.publish_case_scores(result)

    assert client.calls == []


def test_repeated_publish_uses_same_score_ids(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(langfuse_scores, "safe_get_client", lambda: client)
    result = _result()

    langfuse_scores.publish_case_scores(result)
    first_ids = [call["score_id"] for call in client.calls]
    langfuse_scores.publish_case_scores(result)

    assert [call["score_id"] for call in client.calls[3:]] == first_ids
    assert all(len(score_id) == 16 for score_id in first_ids)


def test_disabled_langfuse_does_not_initialize_client_or_publish(monkeypatch):
    import langfuse

    initialized = []
    langfuse_tracing._CLIENT_CACHE["client"] = None
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    monkeypatch.setattr(langfuse, "get_client", lambda: initialized.append(True))

    langfuse_scores.publish_case_scores(_result())

    assert initialized == []
    assert langfuse_tracing.langfuse_enabled() is False


def test_unconfigured_langfuse_is_a_noop(monkeypatch):
    monkeypatch.setattr(langfuse_scores, "safe_get_client", lambda: None)
    result = _result()

    assert langfuse_scores.publish_case_scores(result) is None


def test_score_exception_does_not_mutate_or_raise(monkeypatch):
    client = FakeClient(fail=True)
    monkeypatch.setattr(langfuse_scores, "safe_get_client", lambda: client)
    result = _result()
    original = deepcopy(result)

    langfuse_scores.publish_case_scores(result)

    assert result == original
    assert len(client.calls) == 3

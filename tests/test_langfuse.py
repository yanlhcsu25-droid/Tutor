import os
import time
from uuid import uuid4

import pytest


@pytest.mark.skipif(
    os.getenv("RUN_LANGFUSE_TEST") != "1",
    reason="Langfuse smoke test requires external credentials and network",
)
def test_langfuse_smoke():
    from langfuse import get_client

    langfuse = get_client()
    assert langfuse.auth_check()
    with langfuse.start_as_current_observation(
        as_type="span", name="teacher-agent-smoke-test",
    ) as span:
        span.update(
            input={"message": "测试 Langfuse Trace"},
            output={"status": "ok"},
        )
    langfuse.flush()


@pytest.mark.skipif(
    os.getenv("RUN_LANGFUSE_TEST") != "1",
    reason="Langfuse score test requires external credentials and network",
)
def test_langfuse_boolean_score_is_linked_to_expected_session():
    import langfuse
    from langfuse import get_client

    from evaluation.reliability.langfuse_scores import _score_id, publish_case_scores

    assert langfuse.__version__ == "4.14.4"
    session_id = f"langfuse-score-integration-{uuid4().hex}"
    result = {
        "case_id": "LANGFUSE-SCORE-INTEGRATION",
        "conversation_id": session_id,
        "variant": "state-policy",
        "grader_version": "grader-v1",
        "passed": True,
        "graders": [{"grader": "outcome_integrity", "passed": True}],
    }

    publish_case_scores(result)
    client = get_client()
    client.flush()

    score_id = _score_id(session_id, "tutor_eval_outcome_integrity")
    score = None
    deadline = time.monotonic() + 15
    while score is None and time.monotonic() < deadline:
        try:
            score = client.api.scores.get_by_id(score_id)
        except Exception:
            time.sleep(1)

    assert score is not None
    assert score.session_id == session_id
    assert score.data_type == "BOOLEAN"
    assert score.value == 1.0

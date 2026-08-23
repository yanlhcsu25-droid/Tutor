import os

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

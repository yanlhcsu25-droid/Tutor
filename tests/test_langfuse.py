from langfuse import get_client

langfuse = get_client()

print("auth:", langfuse.auth_check())

with langfuse.start_as_current_observation(
    as_type="span",
    name="teacher-agent-smoke-test",
) as span:
    span.update(
        input={"message": "测试 Langfuse Trace"},
        output={"status": "ok"},
    )

langfuse.flush()

print("Trace sent")
import json

from calculus_agent.runtime.tool_loop import ToolLoop


def test_tool_observation_serializes_exception_values():
    messages = []
    ToolLoop.append_observation(
        messages,
        call_id="call-1",
        name="broken_tool",
        payload={"ok": False, "error": ValueError("bad input")},
    )

    assert json.loads(messages[0]["content"]) == {
        "ok": False,
        "error": "bad input",
    }


def test_tool_observation_preserves_normal_payload():
    messages = []
    ToolLoop.append_observation(
        messages,
        call_id="call-2",
        name="tool",
        payload={"ok": True, "items": [1, "two"]},
    )

    assert json.loads(messages[0]["content"]) == {
        "ok": True,
        "items": [1, "two"],
    }

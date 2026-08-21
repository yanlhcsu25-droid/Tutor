import json

from calculus_agent.runtime.observation_projection import project_tool_observation
from calculus_agent.runtime.tool_loop import ToolLoop


def test_tool_observation_projection_removes_runtime_internals():
    payload = {
        "ok": True,
        "content": "真实业务内容",
        "constraint_provenance": {"internal": True},
        "curriculum_semantic_matches": ["large internal result"],
    }

    projected = project_tool_observation("inspect_curriculum", payload)

    assert projected == {"ok": True, "content": "真实业务内容"}
    assert payload["constraint_provenance"] == {"internal": True}


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

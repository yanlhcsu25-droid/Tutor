import json
from io import BytesIO
from urllib.error import HTTPError

import calculus_agent.runtime.backend as backend_module
from calculus_agent.agent.trace_log import redact_trace_value
from calculus_agent.runtime.backend import BailianChatBackend
from calculus_agent.runtime.observation_projection import (
    observation_size_metrics,
    project_tool_observation,
)
from calculus_agent.runtime.tool_execution import trace_entry
from calculus_agent.runtime.tool_loop import ToolLoop


def test_openai_compatible_adapter_has_explicit_provider_identity():
    backend = BailianChatBackend(
        api_key="test",
        base_url="https://silicon.example/v1",
        model="test-model",
        provider="siliconflow",
    )

    assert backend.provider == "siliconflow"
    assert backend.backend_adapter == "BailianChatBackend"
    assert backend.protocol == "openai_compatible"


def test_openai_compatible_adapter_retries_429_with_retry_after(monkeypatch):
    calls = iter([
        HTTPError("url", 429, "limited", {"Retry-After": "3"}, BytesIO()),
        type("Response", (), {
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: None,
            "read": lambda self: json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "ok"}}]
            }).encode(),
        })(),
    ])
    sleeps = []

    def fake_urlopen(*args, **kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(backend_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(backend_module.time, "sleep", sleeps.append)
    backend = BailianChatBackend(
        api_key="test",
        base_url="https://silicon.example/v1",
        model="test-model",
    )

    assert backend.complete([], [])["message"]["content"] == "ok"
    assert sleeps == [3.0]


def test_openai_compatible_adapter_retries_transport_timeout(monkeypatch):
    calls = iter([
        TimeoutError("read timed out"),
        ConnectionResetError("remote disconnected"),
        type("Response", (), {
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: None,
            "read": lambda self: json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "ok"}}]
            }).encode(),
        })(),
    ])
    sleeps = []

    def fake_urlopen(*args, **kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(backend_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(backend_module.time, "sleep", sleeps.append)
    backend = BailianChatBackend(
        api_key="test",
        base_url="https://silicon.example/v1",
        model="test-model",
        retry_base_seconds=2,
    )

    assert backend.complete([], [])["message"]["content"] == "ok"
    assert sleeps == [2, 4]


class _UsageBackend:
    provider = "test-provider"
    model = "test-model"

    def complete(self, messages, tools):
        return {
            "message": {"role": "assistant", "content": "done"},
            "model": self.model,
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 2},
            },
        }


def test_tool_loop_keeps_return_contract_and_exposes_real_usage_separately():
    backend = _UsageBackend()

    assert ToolLoop.run(backend, [], []) == {"role": "assistant", "content": "done"}
    completion = ToolLoop.run_result(backend, [], [])

    assert completion.message == {"role": "assistant", "content": "done"}
    assert completion.metadata == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "cached_tokens": 3,
        "reasoning_tokens": 2,
        "actual_usage_available": True,
        "provider": "test-provider",
        "model": "test-model",
        "provider_usage_raw": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
            "prompt_tokens_details": {"cached_tokens": 3},
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
        "provider_metadata": None,
    }


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


def test_observation_size_metrics_reports_projection_reduction():
    metrics = observation_size_metrics(
        "inspect_curriculum",
        {"ok": True, "metadata": {"large": "x" * 20}},
    )

    assert metrics["raw_observation_chars"] > metrics["llm_observation_chars"]
    assert metrics["observation_reduction_chars"] > 0


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


def test_trace_payload_serializes_exception_values():
    payload = redact_trace_value({"ok": False, "error": ValueError("bad input")})

    assert json.dumps(payload) == '{"ok": false, "error": "bad input"}'


def test_persisted_trace_entry_serializes_nested_validation_error():
    entry = trace_entry(
        call_id="call-invalid",
        name="create_teaching_design",
        arguments={},
        payload={
            "ok": False,
            "details": [{"ctx": {"error": ValueError("scores must match")}}],
        },
    )

    assert json.dumps(entry)
    assert entry["result"]["details"][0]["ctx"]["error"] == "scores must match"


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

import json

from tests.evals.teaching_quality.grader.provider import (
    JudgeProviderConfig,
    OpenAICompatibleJudgeAdapter,
)


class FakeBackend:
    def __init__(self):
        self.calls = []

    def complete(self, messages, tools, **kwargs):
        self.calls.append((messages, tools, kwargs))
        return {
            "message": {"content": json.dumps({"passed": True})},
            "model": "response-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            "id": "response-id",
            "finish_reason": "stop",
        }


def test_real_model_adapter_requests_json_and_records_provenance():
    backend = FakeBackend()
    config = JudgeProviderConfig(
        provider="test", model="requested-model", temperature=0, max_tokens=321,
        timeout_seconds=12, max_retries=0, retry_base_seconds=0,
    )
    adapter = OpenAICompatibleJudgeAdapter(backend, config)
    assert adapter.complete("prompt") == {"passed": True}
    _, tools, kwargs = backend.calls[0]
    assert tools == []
    assert kwargs == {
        "response_format": {"type": "json_object"},
        "timeout": 12,
        "temperature": 0,
        "max_tokens": 321,
    }
    provenance = adapter.provenance()
    assert provenance["last_call"]["usage"]["total_tokens"] == 13
    assert provenance["last_call"]["response_model"] == "response-model"

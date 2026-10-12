"""Offline provider retry and error classification regressions."""

import json
import ssl
from unittest.mock import patch

import pytest

from calculus_agent.runtime.backend import BailianChatBackend
from calculus_agent.runtime.contracts import RuntimeErrorInfo


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 3},
        }).encode()


def _backend() -> BailianChatBackend:
    return BailianChatBackend(
        api_key="test-api-key-redaction-sentinel",
        base_url="https://provider.invalid/v1",
        model="test-model",
        max_retries=1,
        retry_base_seconds=0,
    )


def test_ssl_eof_retry_records_attempts_without_api_key():
    with patch(
        "calculus_agent.runtime.backend.urlopen",
        side_effect=[ssl.SSLEOFError("unexpected eof"), _Response()],
    ):
        response = _backend().complete([{"role": "user", "content": "x"}], [])

    assert [item["status"] for item in response["provider_attempts"]] == [
        "error", "success",
    ]
    assert response["provider_attempts"][0]["category"] == "provider_connection_error"
    assert "secret-must-not-be-observed" not in json.dumps(response)


def test_exhausted_ssl_error_is_classified_as_provider_not_agent_failure():
    backend = _backend()
    with patch(
        "calculus_agent.runtime.backend.urlopen",
        side_effect=ssl.SSLEOFError("unexpected eof"),
    ), pytest.raises(ssl.SSLEOFError) as caught:
        backend.complete([{"role": "user", "content": "x"}], [])

    error = RuntimeErrorInfo.from_exception(caught.value, stage="llm_call")
    assert error.error_code == "provider_connection_error"
    assert len(caught.value.provider_attempts) == 2

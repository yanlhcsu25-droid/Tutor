import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Protocol


class ChatBackend(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> dict: ...


class OllamaChatBackend:
    provider = "ollama"

    def __init__(self, *, base_url: str, model: str, timeout: float = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "think": False,
                "options": {"temperature": 0},
            },
            ensure_ascii=False,
        ).encode()
        request = Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode())


class BailianChatBackend:
    """Expose an OpenAI-compatible response in the internal chat shape."""

    provider = "bailian"
    backend_adapter = "BailianChatBackend"
    protocol = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120,
        provider: str = "bailian",
        max_retries: int = 4,
        retry_base_seconds: float = 5.0,
    ) -> None:
        self.api_key = api_key
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds

    def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str | dict = "auto",
        response_format: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        optional_payload = {}
        if tools:
            optional_payload["tools"] = tools
            optional_payload["tool_choice"] = tool_choice
        if response_format:
            optional_payload["response_format"] = response_format
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "enable_thinking": False,
                "temperature": 0,
                **optional_payload,
            },
            ensure_ascii=False,
        ).encode()
        request = Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        request_timeout = self.timeout if timeout is None else timeout
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=request_timeout) as response:
                    body = json.loads(response.read().decode())
                break
            except (HTTPError, TimeoutError, URLError) as error:
                retryable = (
                    isinstance(error, TimeoutError)
                    or isinstance(error, URLError) and isinstance(error.reason, TimeoutError)
                    or isinstance(error, HTTPError) and error.code == 429
                )
                if not retryable or attempt == self.max_retries:
                    raise
                retry_after = (
                    error.headers.get("Retry-After")
                    if isinstance(error, HTTPError) and error.headers
                    else None
                )
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.replace(".", "", 1).isdigit()
                    else self.retry_base_seconds * (2**attempt)
                )
                time.sleep(min(delay, 60.0))
        choice = body["choices"][0]
        return {
            "message": choice["message"],
            "finish_reason": choice.get("finish_reason"),
            "model": body.get("model"),
            "provider": self.provider,
            "usage": body.get("usage"),
            "id": body.get("id"),
            "created": body.get("created"),
            "system_fingerprint": body.get("system_fingerprint"),
        }

    def complete_structured(
        self,
        messages: list[dict],
        schema: dict,
    ) -> dict:
        """Request JSON and let the caller validate it against ``schema``."""
        del schema  # Provider JSON mode does not guarantee schema enforcement.
        return self.complete(
            messages,
            [],
            response_format={"type": "json_object"},
            timeout=min(self.timeout, 30),
        )

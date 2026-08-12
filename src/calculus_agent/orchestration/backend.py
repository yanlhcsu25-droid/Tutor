import json
from urllib.request import Request, urlopen


class OllamaChatBackend:
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
    """Expose Bailian's OpenAI-compatible response in the internal chat shape."""

    def __init__(
        self, *, api_key: str, base_url: str, model: str, timeout: float = 120
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "enable_thinking": False,
                "temperature": 0,
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
        with urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode())
        return {"message": body["choices"][0]["message"]}

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from calculus_agent.config import Settings
from calculus_agent.runtime.backend import BailianChatBackend


@dataclass(frozen=True)
class JudgeProviderConfig:
    provider: str
    model: str
    temperature: float = 0
    max_tokens: int = 2048
    timeout_seconds: float = 120
    max_retries: int = 4
    retry_base_seconds: float = 5
    pacing_seconds: float = 0


@dataclass
class JudgeCallMetadata:
    provider: str
    requested_model: str
    response_model: str | None
    temperature: float
    max_tokens: int
    latency_seconds: float
    usage: dict[str, Any] | None
    response_id: str | None
    finish_reason: str | None


class OpenAICompatibleJudgeAdapter:
    """JSON-only adapter shared by semantic and behavioral calibration calls."""

    def __init__(self, backend: BailianChatBackend, config: JudgeProviderConfig) -> None:
        self.backend = backend
        self.config = config
        self.last_call: JudgeCallMetadata | None = None

    def complete(self, prompt: str) -> Mapping[str, Any]:
        if self.config.pacing_seconds:
            time.sleep(self.config.pacing_seconds)
        started = time.perf_counter()
        response = self.backend.complete(
            [
                {"role": "system", "content": "Return exactly one valid JSON object."},
                {"role": "user", "content": prompt},
            ],
            [],
            response_format={"type": "json_object"},
            timeout=self.config.timeout_seconds,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        latency = time.perf_counter() - started
        self.last_call = JudgeCallMetadata(
            provider=self.config.provider,
            requested_model=self.config.model,
            response_model=response.get("model"),
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            latency_seconds=round(latency, 4),
            usage=response.get("usage"),
            response_id=response.get("id"),
            finish_reason=response.get("finish_reason"),
        )
        content = (response.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise TypeError("judge provider returned no text content")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("judge provider returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise TypeError("judge provider JSON must be an object")
        return payload

    def provenance(self) -> dict[str, Any]:
        return {
            "config": asdict(self.config),
            "last_call": asdict(self.last_call) if self.last_call else None,
        }


def build_siliconflow_judge_adapter(
    settings: Settings,
    *,
    temperature: float = 0,
    max_tokens: int = 2048,
    pacing_seconds: float = 0,
) -> OpenAICompatibleJudgeAdapter:
    if not settings.siliconflow_api_key:
        raise ValueError("SILICONFLOW_API_KEY is required for Judge calibration")
    config = JudgeProviderConfig(
        provider="siliconflow",
        model=settings.siliconflow_agent_model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=settings.siliconflow_timeout_seconds,
        max_retries=settings.siliconflow_max_retries,
        retry_base_seconds=settings.siliconflow_retry_base_seconds,
        pacing_seconds=pacing_seconds,
    )
    backend = BailianChatBackend(
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        model=config.model,
        timeout=config.timeout_seconds,
        provider=config.provider,
        max_retries=config.max_retries,
        retry_base_seconds=config.retry_base_seconds,
    )
    return OpenAICompatibleJudgeAdapter(backend, config)

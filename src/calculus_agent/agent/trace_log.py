"""Append-only, best-effort traces for the Teacher Agent debug console."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


DEFAULT_AGENT_TRACE_DIR = Path("logs/agent")

_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|password|passphrase|secret|credential|authorization|"
    r"access[_-]?token|refresh[_-]?token|^token$|^cookie$)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(?:\b(?:api[_ -]?key|password|passphrase|secret|token|authorization)\b|密码|口令)"
    r"\s*(?:[:=：]|\bis\b|是|为)\s*([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def redact_trace_value(value: Any) -> Any:
    """Remove secret-shaped values before data ever reaches the trace payload."""
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY_RE.search(str(key))
            else redact_trace_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_trace_value(item) for item in value]
    if isinstance(value, str):
        value = _SENSITIVE_VALUE_RE.sub("[REDACTED]", value)
        value = _BEARER_RE.sub("Bearer [REDACTED]", value)
        return _OPENAI_KEY_RE.sub("[REDACTED]", value)
    return value


class AgentTraceRecorder:
    """Collect one turn in memory, then append one JSONL record without raising."""

    def __init__(self, directory: Path = DEFAULT_AGENT_TRACE_DIR) -> None:
        self.directory = directory
        self._started_at = datetime.now(UTC)
        self._started_monotonic = time.perf_counter()
        self._record: dict[str, Any] = {
            "trace_version": 1,
            "trace_id": str(uuid4()),
            "started_at": self._started_at.isoformat(),
            "conversation_id": None,
            "paper_id": None,
            "user_input": "",
            "memory_before": None,
            "tool_calls": [],
        }

    def start(
        self,
        *,
        conversation_id: str | None,
        paper_id: str | None,
        user_input: str,
    ) -> None:
        try:
            self._record.update(redact_trace_value({
                "conversation_id": conversation_id,
                "paper_id": paper_id,
                "user_input": user_input,
            }))
        except Exception:
            pass

    def set_memory_before(self, memory: Any) -> None:
        try:
            self._record["memory_before"] = redact_trace_value(memory)
        except Exception:
            pass

    def record_tool_call(
        self,
        *,
        tool_name: str,
        arguments: Any,
        memory_before: Any,
        result: Any,
        memory_after: Any,
    ) -> None:
        try:
            self._record["tool_calls"].append(redact_trace_value({
                "sequence": len(self._record["tool_calls"]) + 1,
                "tool_name": tool_name,
                "arguments": arguments,
                "memory_before": memory_before,
                "result": result,
                "memory_after": memory_after,
            }))
        except Exception:
            pass

    def finish(
        self,
        *,
        agent_status: str,
        final_response: str,
        memory_after: Any,
        paper_id: str | None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist the trace. Any file-system failure is deliberately ignored."""
        try:
            error_fields: dict[str, Any] = {}
            if error:
                redacted = redact_trace_value(dict(error))
                error_fields = {
                    "error_code": redacted.get("error_code"),
                    "error_type": redacted.get("error_type"),
                    "error_message": redacted.get("error_message"),
                    "error_stage": redacted.get("error_stage"),
                }
            completed_at = datetime.now(UTC)
            self._record.update(redact_trace_value({
                "completed_at": completed_at.isoformat(),
                "duration_ms": round((time.perf_counter() - self._started_monotonic) * 1000, 1),
                "status": "failed" if agent_status == "failed" else "success",
                "agent_status": agent_status,
                "final_response": final_response,
                "memory_after": memory_after,
                "paper_id": paper_id,
                **error_fields,
            }))
            self.directory.mkdir(parents=True, exist_ok=True)
            destination = self.directory / f"{self._started_at.date().isoformat()}.jsonl"
            with destination.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(self._record, ensure_ascii=False, default=str))
                handle.write("\n")
        except Exception:
            pass


def read_agent_traces(directory: Path = DEFAULT_AGENT_TRACE_DIR) -> list[dict[str, Any]]:
    """Read valid JSONL records only; a malformed or unreadable file never breaks admin."""
    try:
        files = sorted(directory.glob("*.jsonl"), reverse=True)
    except OSError:
        return []
    traces: list[dict[str, Any]] = []
    for path in files:
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict) and item.get("trace_version") == 1:
                        traces.append(item)
        except OSError:
            continue
    return sorted(traces, key=lambda item: str(item.get("started_at", "")), reverse=True)


def list_agent_trace_sessions(directory: Path = DEFAULT_AGENT_TRACE_DIR) -> list[dict[str, Any]]:
    """Summarize traces by conversation for the admin session list."""
    sessions: dict[str, dict[str, Any]] = {}
    for trace in read_agent_traces(directory):
        conversation_id = trace.get("conversation_id") or "__without_conversation__"
        current = sessions.get(conversation_id)
        if current is None:
            sessions[conversation_id] = {
                "conversation_id": trace.get("conversation_id"),
                "started_at": trace.get("started_at"),
                "last_user_input": trace.get("user_input", ""),
                "status": trace.get("status", "failed"),
                "turn_count": 1,
            }
        else:
            current["turn_count"] += 1
    return sorted(sessions.values(), key=lambda item: str(item["started_at"]), reverse=True)


def list_agent_trace_turns(
    conversation_id: str,
    directory: Path = DEFAULT_AGENT_TRACE_DIR,
) -> list[dict[str, Any]]:
    return [
        trace for trace in read_agent_traces(directory)
        if trace.get("conversation_id") == conversation_id
    ]

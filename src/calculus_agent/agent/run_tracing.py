"""Local, durable run-level tracing for the Teacher Agent.

This module is the **source of truth** for one Teacher Agent execution
(one user turn). It is deliberately independent of Langfuse: every write
goes to the application database through the shared SQLAlchemy ``session``
and is part of the same transaction that the calling code commits. If
Langfuse is unavailable, broken, or slow, the local trace is unaffected.

Design goals (see teacher's Run-Level Tracing spec):

* Every ``/teacher-agent/run`` request gets exactly one ``run_id``.
* The same ``conversation_id`` may contain many ``run_id`` s.
* Every agent / model_call / tool_call / state_transition step is a span
  linked to its ``run_id`` via ``parent_span_id``.
* A tool that *returns* ``{"ok": false}`` is a **business failure**, not a
  tool exception -- its span status stays ``"success"``; the failure lives
  in the span ``output``.
* A genuine exception is a **technical error** -- its span status is
  ``"error"`` and the run carries an ``error`` payload.

All methods are best-effort and swallow exceptions so tracing can never
break business behaviour.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from calculus_agent.models import TeacherAgentSpan, TeacherAgentRunTrace, new_id
from calculus_agent.agent.trace_log import redact_trace_value


class TeacherAgentRunManager:
    """Create and finalize the per-turn run row plus its spans.

    ``create()`` must be called first, as early as possible in the request
    path (before any business logic or early return). If creation fails the
    manager degrades to a no-op so the agent keeps working.
    """

    def __init__(
        self,
        session: Any,
        conversation_id: str | None,
        paper_id: str | None,
        user_input: str,
        agent_name: str = "teacher_agent",
        run_id: str | None = None,
        request_fingerprint: str | None = None,
    ) -> None:
        self.session = session
        self.conversation_id = conversation_id
        self.paper_id = paper_id
        self.user_input = user_input
        self.agent_name = agent_name
        self.requested_run_id = run_id
        self.request_fingerprint = request_fingerprint
        self.run_id: str | None = None
        self.row: TeacherAgentRunTrace | None = None
        self.conflict = False
        self._started_at = datetime.now(UTC)
        self._monotonic = time.perf_counter()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def create(self) -> "TeacherAgentRunManager":
        try:
            with self.session.begin_nested():
                row = TeacherAgentRunTrace(
                    run_id=self.requested_run_id or new_id(),
                    conversation_id=self.conversation_id,
                    paper_id=self.paper_id,
                    user_message=self.user_input,
                    agent_name=self.agent_name,
                    status="received",
                    started_at=self._started_at,
                    result_status="running",
                    request_fingerprint=self.request_fingerprint,
                )
                self.session.add(row)
                self.session.flush()
            self.row = row
            self.run_id = row.run_id
        except IntegrityError:
            self.row = None
            self.run_id = self.requested_run_id
            self.conflict = True
        except Exception:
            self.row = None
            self.run_id = None
        return self

    def mark_running(self) -> None:
        if self.row is None:
            return
        try:
            self.row.status = "running"
            self.session.flush()
        except Exception:
            pass

    def finalize(
        self,
        *,
        status: str,
        final_response: str | None,
        state_after: Any | None = None,
        paper_id: str | None = None,
        error: dict[str, Any] | None = None,
        result_json: dict[str, Any] | None = None,
    ) -> None:
        if self.row is None:
            return
        try:
            ended = datetime.now(UTC)
            self.row.status = status
            self.row.result_status = status
            self.row.final_response = final_response
            self.row.ended_at = ended
            self.row.latency_ms = int((ended - self._started_at).total_seconds() * 1000)
            if paper_id is not None:
                self.row.paper_id = paper_id
            if state_after is not None:
                self.row.state_after_json = redact_trace_value(state_after)
            if result_json is not None:
                self.row.result_json = result_json
            if error:
                # Genuine technical error only. Business failures (normal return
                # with status="failed", e.g. insufficient_candidates / model
                # unavailable) are NOT recorded here -- they live in status,
                # final_response, and tool_calls_json (ok=false). This keeps the
                # error_* columns strictly a technical-error signal.
                self.row.error_code = error.get("error_code")
                self.row.error_type = error.get("error_type")
                self.row.error_message = error.get("error_message")
                self.row.error_stage = error.get("error_stage")
            self.session.flush()
        except Exception:
            pass

    # ── state ──────────────────────────────────────────────────────────────

    def set_state_before(self, before: Any | None) -> None:
        if self.row is None:
            return
        try:
            self.row.state_before_json = redact_trace_value(before)
            self.session.flush()
        except Exception:
            pass

    # ── spans ──────────────────────────────────────────────────────────────

    def add_span(
        self,
        span_type: str,
        name: str,
        *,
        parent_span_id: str | None = None,
        status: str = "running",
        input: Any | None = None,
        output: Any | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> TeacherAgentSpan | None:
        if self.row is None:
            return None
        try:
            _started = started_at or datetime.now(UTC)
            span = TeacherAgentSpan(
                span_id=new_id(),
                run_id=self.run_id,
                parent_span_id=parent_span_id,
                span_type=span_type,
                name=name,
                status=status,
                started_at=_started,
                ended_at=ended_at,
                input_json=redact_trace_value(input) if input is not None else None,
                output_json=redact_trace_value(output) if output is not None else None,
            )
            if ended_at is not None:
                span.latency_ms = int((ended_at - _started).total_seconds() * 1000)
            self.session.add(span)
            self.session.flush()
            return span
        except Exception:
            return None

    def update_span(
        self,
        span: TeacherAgentSpan | None,
        *,
        status: str | None = None,
        output: Any | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        if span is None or self.row is None:
            return
        try:
            if status is not None:
                span.status = status
            if output is not None:
                span.output_json = redact_trace_value(output)
            if ended_at is not None:
                span.ended_at = ended_at
            if span.started_at is not None and span.ended_at is not None:
                span.latency_ms = int(
                    (span.ended_at - span.started_at).total_seconds() * 1000
                )
            self.session.flush()
        except Exception:
            pass

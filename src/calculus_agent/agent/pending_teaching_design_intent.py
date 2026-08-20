"""Deterministic-first intent boundary for an awaiting TeachingDesign."""

from __future__ import annotations

import re
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class PendingTeachingDesignIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm", "revise", "cancel", "query", "ambiguous"]
    revision_request: str | None = None


_CONFIRM = re.compile(
    r"^(?:确认|可以(?:[，,]?(?:就这样|就按这个(?:来)?))?|"
    r"就这样|按这个(?:来)?|同意|没问题|好的?)\s*[。！.!！]*$"
)
_CANCEL = re.compile(r"取消|算了|不要(?:这个|了)?|放弃|作废")
_QUERY = re.compile(r"为什么|为何|怎么安排|如何安排|解释一下|说明一下|方案里")
_REVISE = re.compile(
    r"基础一点|简单一点|容易一点|难一点|重点|少讲|多讲|少做|多做|"
    r"课时|修改|调整|改成|增加|减少|换成|突出|删掉|补充|强化"
)


def classify_pending_teaching_design_intent(message: str) -> PendingTeachingDesignIntent:
    """Classify only the next action on an awaiting design.

    Confirmation is intentionally strict: any revision marker wins over a
    conversational "可以" so a request containing a new teaching requirement
    can never be confirmed accidentally.
    """
    text = (message or "").strip()
    if _CANCEL.search(text):
        return PendingTeachingDesignIntent(action="cancel")
    if _REVISE.search(text):
        return PendingTeachingDesignIntent(
            action="revise",
            revision_request=text,
        )
    if _CONFIRM.fullmatch(text) or (
        ("确认" in text or "按这个" in text)
        and not _QUERY.search(text)
    ):
        return PendingTeachingDesignIntent(action="confirm")
    if _QUERY.search(text):
        return PendingTeachingDesignIntent(action="query")
    return PendingTeachingDesignIntent(action="ambiguous")


def resolve_pending_teaching_design_intent(
    message: str,
    *,
    backend: Any | None = None,
) -> PendingTeachingDesignIntent:
    """Use a small JSON-only model fallback after deterministic classification."""
    deterministic = classify_pending_teaching_design_intent(message)
    if deterministic.action != "ambiguous" or backend is None:
        return deterministic
    try:
        raw = backend.complete([
            {
                "role": "system",
                "content": (
                    "Classify the teacher's next action on an awaiting teaching design. "
                    "Return JSON only with action one of confirm, revise, cancel, query, ambiguous "
                    "and optional revision_request. Any new teaching requirement means revise."
                ),
            },
            {"role": "user", "content": message},
        ], [])
        response = raw.get("message", raw) if isinstance(raw, dict) else {}
        content = response.get("content") if isinstance(response, dict) else None
        if not isinstance(content, str):
            return deterministic
        content = content.strip().removeprefix("```json").removesuffix("```").strip()
        parsed = json.loads(content)
        return PendingTeachingDesignIntent.model_validate(parsed)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return deterministic

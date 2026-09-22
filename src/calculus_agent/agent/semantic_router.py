"""Thin structured-LLM adapter for top-level Teacher Agent routing."""

from __future__ import annotations

import json
import logging
from typing import Any

from calculus_agent.agent.task_router import TaskRoute

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Classify the teacher's message into exactly one top-level capability.
Return JSON matching the supplied TaskRoute schema.

Definitions:
- DIRECT_ACTION: explicitly execute paper generation, paper read/change/version work, or continue a pending action.
- TEACHING_DESIGN: explicitly create a persistent, executable teaching design, plan, course, or training-path artifact.
- TEACHING_PLANNING: analyze learning problems or discuss preparation, review arrangements, and training ideas without explicitly requesting a persistent TeachingDesign artifact.
- INFORMATION_REQUEST: explain knowledge or consult curriculum, question-bank, or general teaching methods without requesting a design artifact.

Rules:
- Classify only the top-level capability. Do not parse chapters, counts, scores, knowledge points, difficulty, or any Tool arguments.
- Do not output Tool names and do not claim database facts.
- Do not infer an action merely because one character or word such as 出, 改, or 题 appears.
- Respect negation: a denied action is not the requested action.
- A question asking how to teach, explain, or review a topic is INFORMATION_REQUEST unless it asks you to produce a plan or artifact.
- clarification_needed is a semantic decision, not a confidence threshold. It MUST be true when the object or intended action is omitted—for example, merely wanting to "target something before an exam" or asking how to "handle this part" without usable context.
- If multiple capabilities remain reasonably possible, set clarification_needed=true and ask one concise question.
- artifact_required must be true only for TEACHING_DESIGN.
- reason should briefly explain the semantic distinction, not execution details.
"""


def resolve_semantic_task_route(message: str, *, backend: Any) -> TaskRoute | None:
    """Return a validated LLM route, or ``None`` for safe heuristic fallback."""
    complete_structured = getattr(backend, "complete_structured", None)
    if not callable(complete_structured):
        return None
    try:
        raw = complete_structured(
            [
                {
                    "role": "system",
                    "content": (
                        _SYSTEM_PROMPT
                        + "\nJSON Schema:\n"
                        + json.dumps(TaskRoute.model_json_schema(), ensure_ascii=False)
                    ),
                },
                {"role": "user", "content": message},
            ],
            TaskRoute.model_json_schema(),
        )
        response = raw.get("message", raw) if isinstance(raw, dict) else {}
        content = response.get("content") if isinstance(response, dict) else None
        if not isinstance(content, str):
            return None
        content = content.strip().removeprefix("```json").removesuffix("```").strip()
        return TaskRoute.model_validate(json.loads(content))
    except Exception as exc:  # Backend, JSON, and schema failures all degrade safely.
        logger.warning("semantic task routing failed; using heuristic fallback: %s", exc)
        return None

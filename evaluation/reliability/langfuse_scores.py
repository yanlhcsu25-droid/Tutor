"""Publish deterministic reliability-evaluation scores to Langfuse."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from calculus_agent.agent.langfuse_tracing import safe_get_client

logger = logging.getLogger(__name__)

_SCORE_PREFIX = "tutor_eval_"


def _score_id(conversation_id: str, score_name: str) -> str:
    seed = f"tutor-eval:{conversation_id}:{score_name}:v1"
    # Langfuse observation/score IDs are 16 lowercase hex characters. SDK
    # 4.14.4 exposes the deterministic observation-ID helper only privately.
    return hashlib.sha256(seed.encode("utf-8")).digest()[:8].hex()


def _metadata(
    result: dict[str, Any],
    *,
    grader_name: str,
    grader: dict[str, Any] | None = None,
) -> dict[str, Any]:
    values = {
        "case_id": result.get("case_id"),
        "grader_name": grader_name,
        "variant": result.get("variant"),
        "grader_version": (grader or {}).get("grader_version")
        or result.get("grader_version"),
    }
    return {key: value for key, value in values.items() if value is not None}


def _publish_boolean(
    client: Any,
    result: dict[str, Any],
    *,
    score_name: str,
    passed: bool,
    grader_name: str,
    grader: dict[str, Any] | None = None,
) -> None:
    conversation_id = result["conversation_id"]
    try:
        client.create_score(
            name=score_name,
            value=1.0 if passed else 0.0,
            session_id=conversation_id,
            score_id=_score_id(conversation_id, score_name),
            data_type="BOOLEAN",
            comment=f"{grader_name} {'passed' if passed else 'failed'}",
            metadata=_metadata(result, grader_name=grader_name, grader=grader),
        )
    except Exception:
        logger.exception(
            "Failed to enqueue Langfuse score %s for case %s",
            score_name,
            result.get("case_id"),
        )


def publish_case_scores(result: dict[str, Any]) -> None:
    """Publish one case score and its applicable grader scores; never raises."""
    client = safe_get_client()
    if client is None:
        return

    conversation_id = result.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        logger.warning("Skipping Langfuse scores: missing conversation_id")
        return

    case_passed = result.get("passed")
    if isinstance(case_passed, bool):
        _publish_boolean(
            client,
            result,
            score_name="tutor_eval_case_passed",
            passed=case_passed,
            grader_name="case",
        )
    else:
        logger.warning(
            "Skipping Langfuse case score for %s: passed must be boolean",
            result.get("case_id"),
        )

    graders = result.get("graders") or []
    if not isinstance(graders, list):
        logger.warning("Skipping Langfuse grader scores: graders must be a list")
        return

    for grader in graders:
        if not isinstance(grader, dict):
            logger.warning("Skipping malformed Langfuse grader result")
            continue
        if grader.get("skipped") is True:
            continue

        grader_name = grader.get("grader")
        if not isinstance(grader_name, str) or not grader_name:
            logger.warning("Skipping Langfuse grader score: missing grader name")
            continue
        # This grader defines eligible=False when neither expected nor actual
        # behavior involves clarification, so the grade is not applicable.
        if grader_name == "clarification_contract" and grader.get("eligible") is False:
            continue

        passed = grader.get("passed")
        if not isinstance(passed, bool):
            logger.warning(
                "Skipping Langfuse grader score %s: passed must be boolean",
                grader_name,
            )
            continue

        _publish_boolean(
            client,
            result,
            score_name=f"{_SCORE_PREFIX}{grader_name}",
            passed=passed,
            grader_name=grader_name,
            grader=grader,
        )


def flush_case_scores() -> None:
    """Flush the shared Langfuse client once after the evaluation suite."""
    client = safe_get_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        logger.exception("Failed to flush Langfuse evaluation scores")

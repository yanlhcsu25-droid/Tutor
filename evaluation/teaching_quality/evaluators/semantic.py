from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from .models import SemanticAssertionDecision

SEMANTIC_EVALUATOR_VERSION = "1.0"
SUPPORTED_SEMANTIC_TYPES = {
    "required_content",
    "forbidden_content",
    "source_grounded",
    "reference_used",
    "insufficiency_disclosed",
}
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts/semantic_assertion_judge.md"


class SemanticBackend(Protocol):
    """Injected provider adapter. Implementations must return JSON, not quality scores."""

    def complete(self, prompt: str) -> str | Mapping[str, Any]: ...


class SemanticEvaluatorError(RuntimeError):
    pass


# TQ_REFERENCE_SCOPE_V1

def _reference_only_setup(assertion: Mapping[str, Any], setup: Mapping[str, Any]) -> dict[str, Any]:
    """Limit reference_used to its declared reference, not unrelated setup facts.

    Behavioral/source-grounded evaluators remain responsible for profile quality.
    """
    reference = assertion.get("expected")
    if isinstance(reference, str):
        cursor: Any = setup
        for part in reference.split("."):
            if not isinstance(cursor, Mapping) or part not in cursor:
                break
            cursor = cursor[part]
        else:
            reference = cursor
    return {"reference": reference}


class ModelBackedSemanticAssertionEvaluator:
    """Strict semantic-fact evaluator; provider invocation is supplied externally."""

    def __init__(self, backend: SemanticBackend) -> None:
        self.backend = backend

    def evaluate(
        self,
        assertion: Mapping[str, Any],
        actual: Mapping[str, Any],
        setup: Mapping[str, Any],
    ) -> SemanticAssertionDecision:
        assertion_type = str(assertion.get("type"))
        if assertion_type not in SUPPORTED_SEMANTIC_TYPES:
            raise SemanticEvaluatorError(f"unsupported semantic assertion type: {assertion_type}")
        payload = {
            "evaluator_version": SEMANTIC_EVALUATOR_VERSION,
            "assertion": dict(assertion),
            "setup": _reference_only_setup(assertion, setup) if assertion_type == "reference_used" else dict(setup),
            "actual_output": dict(actual),
        }
        prompt = (
            PROMPT_PATH.read_text().rstrip()
            + "\n\nINPUT_JSON\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
            + "\n"
        )
        raw = self.backend.complete(prompt)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SemanticEvaluatorError("semantic evaluator returned malformed JSON") from exc
        try:
            return SemanticAssertionDecision.model_validate(raw)
        except Exception as exc:
            raise SemanticEvaluatorError("semantic evaluator output violates its schema") from exc

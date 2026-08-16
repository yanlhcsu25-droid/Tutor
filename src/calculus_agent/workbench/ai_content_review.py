from __future__ import annotations

import json
import re
import socket
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.knowledge.classification import build_knowledge_llm_backend
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    Textbook,
)
from calculus_agent.workbench.markdown_schema import payload_from_markdown


class ChatBackend(Protocol):
    def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str | dict = "auto",
        response_format: dict | None = None,
    ) -> dict: ...


class ContentAuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verdict: Literal["PASS", "REVIEW"]
    answer_relevant: bool
    conclusion_consistent: bool
    no_cross_question: bool
    derivation_complete: bool
    confidence: float = Field(ge=0, le=1)
    risk_codes: list[str]
    reason: str

    @property
    def passed(self) -> bool:
        return (
            self.verdict == "PASS"
            and self.answer_relevant
            and self.conclusion_consistent
            and self.no_cross_question
            and self.derivation_complete
            and not self.risk_codes
        )


class DifficultyClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    difficulty_level: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0, le=1)
    needs_review: bool
    reason: str


_OCR_PLACEHOLDER_RE = re.compile(
    r"图片内容暂未解析|暂未解析，请人工核对|OCR\s*占位|\[IMAGE\]|<image>",
    re.IGNORECASE,
)


def deterministic_content_issues(question: dict[str, Any]) -> tuple[list[str], Any | None]:
    """返回阻止自动发布的确定性问题，以及解析成功的 payload。"""
    issues: list[str] = []
    if question.get("match_status") != "matched":
        issues.append("match_status_not_matched")
    payload, validation = payload_from_markdown(
        question.get("edited_markdown") or "",
        question_id=question.get("question_id") or "unknown",
        source_file_id=question.get("source_file_id") or "unknown",
        ocr_markdown=question.get("ocr_markdown") or "",
        source_bbox=question.get("source_bbox"),
    )
    if payload is None or not validation.valid:
        issues.extend(
            f"invalid_{item.field}" for item in validation.issues
        )
        return list(dict.fromkeys(issues)), None
    body = payload.question_content.strip()
    solution = payload.solution_content.strip()
    if len(body) < 8:
        issues.append("question_empty_or_truncated")
    if not solution:
        issues.append("answer_empty")
    if not payload.question_type:
        issues.append("question_type_missing")
    if _OCR_PLACEHOLDER_RE.search(body) or _OCR_PLACEHOLDER_RE.search(solution):
        issues.append("ocr_placeholder_remaining")
    if payload.review_notes.strip():
        issues.append("review_note_present")
    if payload.question_type in {"选择题", "多选题"}:
        labels = sorted(payload.options)
        if labels != ["A", "B", "C", "D"]:
            issues.append("selection_options_abnormal")
    if validation.warnings:
        issues.extend(f"warning_{item.field}" for item in validation.warnings)
    return list(dict.fromkeys(issues)), payload


def estimate_difficulty(question_body: str, solution: str, knowledge_count: int) -> int:
    """保守、确定性的首版难度画像；不使用 LLM 自报 confidence。"""
    step_markers = len(re.findall(r"首先|然后|因此|故|从而|令|设|证明", solution))
    math_density = len(re.findall(r"\\(?:int|lim|sum|frac|sqrt)|\$", question_body + solution))
    score = 1
    score += len(solution) >= 180
    score += len(solution) >= 500 or step_markers >= 4
    score += knowledge_count >= 2 or math_density >= 18
    return max(1, min(int(score), 5))


def recommend_difficulty_with_llm(
    session: Session,
    *,
    question_body: str,
    standard_solution: str,
    question_type: str,
    knowledge_ids: list[str],
    backend: ChatBackend | None = None,
    max_examples: int = 6,
) -> dict[str, Any]:
    """Recommend difficulty with optional human-calibrated few-shot examples.

    A new textbook may legitimately have no human labels yet.  In that case the
    model still returns a rubric-based recommendation, but Python marks it for
    review because the 1-5 scale has not been calibrated to this teacher.
    """
    examples = _difficulty_examples(
        session,
        question_type=question_type,
        knowledge_ids=knowledge_ids,
        target_length=len(question_body),
        limit=max_examples,
    )
    fallback_level = estimate_difficulty(
        question_body,
        standard_solution,
        len(knowledge_ids),
    )
    selected_backend = backend or build_knowledge_llm_backend()
    if selected_backend is None:
        return _difficulty_failure("llm_unavailable", fallback_level, examples)

    schema = DifficultyClassificationResult.model_json_schema()
    calibration_instruction = (
        "历史样例中的 difficulty_level 是教师确认的标尺，优先用知识点相近、题型相同的样例校准。"
        if examples
        else "当前教材尚无教师确认的难度样例，请仅依据统一评分标准给出首轮建议，并设置 needs_review=true。"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是高等数学题库难度标注员。根据解题所需的概念深度、策略选择、推理步骤、"
                "计算负担和综合程度，把题目评为1到5级。"
                f"{calibration_instruction}不要按题干长度机械判断。"
                "1=定义或单步基础题；2=常规单方法题；3=多步或小型综合题；"
                "4=需要非直接策略或多个知识点协同；5=复杂综合、关键洞察或高难证明。"
                "样例不足或相互矛盾时 needs_review=true。confidence 只是判断把握度，不是统计准确率。"
                "严格按 JSON Schema 返回。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "target": {
                        "question_type": question_type,
                        "question_body": question_body,
                        "standard_solution": standard_solution,
                        "knowledge_points": _knowledge_names(session, knowledge_ids),
                    },
                    "human_labeled_examples": examples,
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        response = selected_backend.complete(
            messages,
            [],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "question_difficulty_classification",
                    "schema": schema,
                },
            },
        )
        raw, raw_type = _extract_raw(response)
        result = DifficultyClassificationResult.model_validate(_decode_json(raw))
        result_payload = result.model_dump()
        if not examples:
            # A zero-shot score is useful as an editable suggestion, but it is
            # not calibrated against this teacher's historical scale.
            result_payload["needs_review"] = True
        return {
            **result_payload,
            "provenance": "llm_suggested",
            "fallback_reason": None,
            "raw_response_type": raw_type,
            "model": response.get("model") if isinstance(response, dict) else None,
            "example_count": len(examples),
        }
    except (TimeoutError, socket.timeout):
        return _difficulty_failure("llm_timeout", fallback_level, examples)
    except (HTTPError, URLError, OSError):
        return _difficulty_failure("api_error", fallback_level, examples)
    except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return _difficulty_failure("invalid_structured_output", fallback_level, examples)


def _difficulty_examples(
    session: Session,
    *,
    question_type: str,
    knowledge_ids: list[str],
    target_length: int,
    limit: int,
) -> list[dict[str, Any]]:
    textbook_ids = set(session.scalars(
        select(CurriculumNode.textbook_id)
        .join(KnowledgeNode, KnowledgeNode.curriculum_node_id == CurriculumNode.id)
        .where(KnowledgeNode.id.in_(knowledge_ids))
    ).all())
    if not textbook_ids:
        return []
    active_knowledge_ids = set(session.scalars(
        select(KnowledgeNode.id)
        .join(CurriculumNode, CurriculumNode.id == KnowledgeNode.curriculum_node_id)
        .join(Textbook, Textbook.id == CurriculumNode.textbook_id)
        .where(
            Textbook.id.in_(textbook_ids),
            Textbook.is_active.is_(True),
            KnowledgeNode.review_status == "approved",
        )
    ).all())
    profiles = list(session.scalars(
        select(QuestionProfile)
        .where(
            QuestionProfile.profile_status == "approved",
            QuestionProfile.profile_source == "human",
        )
        .order_by(
            QuestionProfile.created_at.desc(),
            QuestionProfile.profile_version.desc(),
        )
    ).all())
    latest: dict[str, QuestionProfile] = {}
    for profile in profiles:
        latest.setdefault(profile.question_id, profile)
    if not latest:
        return []

    links: dict[str, list[str]] = {}
    for question_id, knowledge_id in session.execute(
        select(
            QuestionKnowledgeLink.question_id,
            QuestionKnowledgeLink.knowledge_node_id,
        ).where(QuestionKnowledgeLink.question_id.in_(latest))
    ).all():
        if knowledge_id in active_knowledge_ids:
            links.setdefault(question_id, []).append(knowledge_id)
    questions = {
        item.id: item
        for item in session.scalars(
            select(Question).where(
                Question.id.in_(latest),
                Question.review_status == "approved",
                Question.is_active.is_(True),
            )
        ).all()
    }
    drafts = {
        item.id: item
        for item in session.scalars(
            select(QuestionDraft).where(
                QuestionDraft.id.in_([item.draft_id for item in questions.values()])
            )
        ).all()
    }
    target_ids = set(knowledge_ids)
    ranked: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for question_id, profile in latest.items():
        question = questions.get(question_id)
        example_ids = links.get(question_id, [])
        if question is None or not example_ids:
            continue
        overlap = len(target_ids.intersection(example_ids))
        same_type = int(question.question_type == question_type)
        length_gap = abs(len(question.question_text) - target_length)
        draft = drafts.get(question.draft_id)
        ranked.append((
            (overlap, same_type, -length_gap),
            {
                "question_type": question.question_type,
                "question_body": question.question_text[:1200],
                "standard_solution": (
                    draft.solution_text if draft and draft.solution_text else ""
                )[:1600],
                "knowledge_points": _knowledge_names(session, example_ids),
                "difficulty_level": profile.difficulty,
            },
        ))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [item for _score, item in ranked[: max(1, limit)]]


def _knowledge_names(session: Session, knowledge_ids: list[str]) -> list[str]:
    nodes = {
        item.id: item.name
        for item in session.scalars(
            select(KnowledgeNode).where(KnowledgeNode.id.in_(knowledge_ids))
        ).all()
    }
    return [nodes[item] for item in knowledge_ids if item in nodes]


def _difficulty_failure(
    reason: str,
    fallback_level: int,
    examples: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "difficulty_level": fallback_level,
        "confidence": 0.0,
        "needs_review": True,
        "reason": "AI 难度推荐未完成，规则估算仅供人工参考",
        "provenance": "rule_fallback",
        "fallback_reason": reason,
        "raw_response_type": None,
        "model": None,
        "example_count": len(examples),
    }


def audit_content_with_llm(
    *,
    question_body: str,
    standard_solution: str,
    question_type: str,
    backend: ChatBackend | None = None,
) -> dict[str, Any]:
    selected_backend = backend or build_knowledge_llm_backend()
    if selected_backend is None:
        return _failure("llm_unavailable")
    schema = ContentAuditResult.model_json_schema()
    messages = [
        {
            "role": "system",
            "content": (
                "你是高等数学题库内容审核员。只判断题目与参考解答的语义一致性和完整性："
                "答案是否在回答本题、答案结论与解析是否一致、是否明显串题、关键推导是否明显缺失。"
                "不要因为解法与你偏好的解法不同而拒绝；不要修改题目；发现任何明确风险时 verdict=REVIEW。"
                "confidence 仅表示判断把握度，不是统计准确率。严格按 JSON Schema 返回。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question_type": question_type,
                    "question_body": question_body,
                    "standard_solution": standard_solution,
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        response = selected_backend.complete(
            messages,
            [],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "question_content_audit", "schema": schema},
            },
        )
        raw, raw_type = _extract_raw(response)
        result = ContentAuditResult.model_validate(_decode_json(raw))
        model_name = response.get("model") if isinstance(response, dict) else None
        return {
            **result.model_dump(),
            "passed": result.passed,
            "fallback_reason": None,
            "raw_response_type": raw_type,
            "model": model_name,
        }
    except (TimeoutError, socket.timeout):
        return _failure("llm_timeout")
    except (HTTPError, URLError, OSError):
        return _failure("api_error")
    except (ValidationError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return _failure("invalid_structured_output")


def _extract_raw(response: dict[str, Any]) -> tuple[Any, str]:
    if not isinstance(response, dict) or not response:
        raise ValueError("empty response")
    if response.get("finish_reason") in {"length", "max_tokens"}:
        raise ValueError("truncated response")
    message = response.get("message", response)
    if not isinstance(message, dict):
        raise ValueError("missing message")
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        return tool_calls[0]["function"]["arguments"], "tool_calls"
    if message.get("parsed") is not None:
        return message["parsed"], "parsed"
    if response.get("parsed") is not None:
        return response["parsed"], "parsed"
    return message.get("content"), "content"


def _decode_json(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("empty structured output")
    value = raw.strip()
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        value = "\n".join(lines[1:-1]).strip()
    return json.loads(value)


def _failure(reason: str) -> dict[str, Any]:
    return {
        "verdict": "REVIEW",
        "answer_relevant": False,
        "conclusion_consistent": False,
        "no_cross_question": False,
        "derivation_complete": False,
        "confidence": 0.0,
        "risk_codes": [reason],
        "reason": "AI 内容审核未完成，必须人工检查",
        "passed": False,
        "fallback_reason": reason,
        "raw_response_type": None,
        "model": None,
    }

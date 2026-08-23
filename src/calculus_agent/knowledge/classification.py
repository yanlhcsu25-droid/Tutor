from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from calculus_agent.config import get_settings
from calculus_agent.knowledge.normalization import normalize_name, terms
from calculus_agent.questions.chapter_assignment import (
    sync_question_chapter_ownership,
)
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeAlias,
    KnowledgeNode,
    Question,
    QuestionKnowledgeLink,
    QuestionKnowledgeReview,
    Textbook,
)
from calculus_agent.runtime.backend import BailianChatBackend


@dataclass(frozen=True)
class TaxonomyItem:
    name: str
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]


CALCULUS_TAXONOMY = (
    TaxonomyItem("函数极限", ("极限",), (r"\lim", "函数极限", "趋于")),
    TaxonomyItem("数列极限", (), (r"n\to", "数列极限", "n趋于")),
    TaxonomyItem("左右极限", ("单侧极限",), (r"^-", r"^+", "左极限", "右极限")),
    TaxonomyItem("极限运算法则", (), ("极限运算法则", "极限号", "商的极限", "积的极限")),
    TaxonomyItem("两个重要极限", ("重要极限",), (r"\frac{\sin", r"1+\frac{1}", "重要极限")),
    TaxonomyItem("无穷小与无穷大", ("无穷小", "无穷大"), ("无穷小", "无穷大", "等价无穷小")),
    TaxonomyItem("函数连续性", ("连续",), ("连续", "连续函数", "连续区间")),
    TaxonomyItem("间断点及其分类", ("间断点",), ("间断点", "跳跃间断", "可去间断", "无穷间断")),
    TaxonomyItem(
        "导数定义",
        ("导数",),
        ("导数定义", "可导", "导数", "差商"),
    ),
    TaxonomyItem("复合函数求导", ("链式法则",), ("复合函数求导", "链式法则")),
    TaxonomyItem("隐函数与参数方程求导", ("隐函数求导",), ("隐函数", "参数方程", "对数求导")),
    TaxonomyItem("微分中值定理", ("中值定理",), ("罗尔定理", "拉格朗日", "柯西中值")),
    TaxonomyItem(
        "洛必达法则",
        (),
        ("洛必达", "L'Hospital", "分子分母求导", "分子分母分别求导"),
    ),
    TaxonomyItem("泰勒公式", ("泰勒展开",), ("泰勒", "麦克劳林", "展开式")),
    TaxonomyItem("函数单调性与极值", ("单调性", "极值"), ("单调", "极值", "驻点")),
    TaxonomyItem("函数凹凸性与拐点", ("凹凸性",), ("凹凸", "拐点")),
    TaxonomyItem("不定积分", (), ("不定积分", "原函数", r"\int")),
    TaxonomyItem("定积分", (), ("定积分", "积分上限", "积分下限")),
    TaxonomyItem("换元积分法", ("换元法",), ("换元积分", "令", "变量代换")),
    TaxonomyItem("分部积分法", ("分部积分",), ("分部积分",)),
    TaxonomyItem("反常积分", ("广义积分",), ("反常积分", "广义积分")),
)


class CandidateKnowledgePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    score: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)


class KnowledgeClassificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    primary_knowledge_point_id: str | None
    secondary_knowledge_point_ids: list[str]
    confidence: float = Field(ge=0, le=1)
    needs_review: bool
    reason: str


class ChatBackend(Protocol):
    def complete(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str | dict = "auto",
        response_format: dict | None = None,
    ) -> dict: ...


class StructuredOutputError(ValueError):
    def __init__(
        self,
        fallback_reason: str,
        raw_response_type: str,
        *,
        validation_errors: list[dict] | None = None,
    ) -> None:
        super().__init__(fallback_reason)
        self.fallback_reason = fallback_reason
        self.raw_response_type = raw_response_type
        self.validation_errors = validation_errors or []


class BusinessValidationError(ValueError):
    pass


def ensure_calculus_taxonomy(session: Session) -> list[KnowledgeNode]:
    existing = {
        node.normalized_name: node
        for node in session.scalars(select(KnowledgeNode)).all()
    }
    output: list[KnowledgeNode] = []
    for item in CALCULUS_TAXONOMY:
        normalized = normalize_name(item.name)
        node = existing.get(normalized)
        if node is None:
            node = KnowledgeNode(
                node_type="knowledge_point",
                name=item.name,
                normalized_name=normalized,
                description="高等数学受控知识点",
                source_type="system_taxonomy",
                confidence=1.0,
                review_status="approved",
            )
            session.add(node)
            session.flush()
            for alias in item.aliases:
                session.add(KnowledgeAlias(
                    node_id=node.id,
                    alias=alias,
                    normalized_alias=normalize_name(alias),
                ))
            existing[normalized] = node
        output.append(node)
    session.flush()
    return output


def current_textbook_taxonomy(session: Session) -> list[KnowledgeNode]:
    """Return the approved, textbook-wide taxonomy; never apply paper chapter scope."""
    active_textbook_id = session.scalar(
        select(Textbook.id)
        .where(Textbook.is_active.is_(True))
        .order_by(Textbook.created_at.desc(), Textbook.id)
        .limit(1)
    )
    if active_textbook_id:
        return list(session.scalars(
            select(KnowledgeNode)
            .join(CurriculumNode, KnowledgeNode.curriculum_node_id == CurriculumNode.id)
            .where(
                CurriculumNode.textbook_id == active_textbook_id,
                CurriculumNode.review_status == "approved",
                KnowledgeNode.review_status == "approved",
            )
            .order_by(CurriculumNode.sort_order, KnowledgeNode.name)
        ).all())
    # Compatibility for installations that predate Textbook/CurriculumNode.
    return [node for node in ensure_calculus_taxonomy(session) if node.review_status == "approved"]


def current_taxonomy_knowledge_nodes(session: Session) -> list[KnowledgeNode]:
    """KnowledgeNodes eligible as knowledge-preference resolver candidates.

    This is the resolver's single source of truth, mirroring
    :func:`current_textbook_taxonomy` so the two call sites cannot drift.

    Eligibility rules:
      - ``curriculum_node_id IS NULL`` -> still listed; the resolver reports
        ``knowledge_scope_uncertain`` (the node exists but has no chapter).
      - ``curriculum_node_id`` references an existing, approved ``CurriculumNode``
        that belongs to the active textbook (when one is configured) -> normal
        candidate.

    KnowledgeNodes whose ``curriculum_node_id`` points to a deleted/missing
    ``CurriculumNode`` are stale (e.g. left behind by a taxonomy replace) and are
    excluded, so they cannot masquerade as valid alternatives and force a spurious
    ``knowledge_ambiguous``. ``source_type`` is intentionally ignored: validity is
    decided by referential integrity (the curriculum node actually exists in the
    current textbook), not by a historical source label.
    """
    curriculum_by_id = {node.id: node for node in session.scalars(select(CurriculumNode))}
    active_textbook_id = session.scalar(
        select(Textbook.id)
        .where(Textbook.is_active.is_(True))
        .order_by(Textbook.created_at.desc(), Textbook.id)
        .limit(1)
    )
    result: list[KnowledgeNode] = []
    for node in session.scalars(select(KnowledgeNode)):
        # Only approved nodes participate at all (regardless of scoping).
        if node.review_status != "approved":
            continue
        cn_id = node.curriculum_node_id
        if cn_id is None:
            # Unscoped node: exists but has no chapter -> resolver reports
            # knowledge_scope_uncertain (not ambiguous, not unknown).
            result.append(node)
            continue
        cn = curriculum_by_id.get(cn_id)
        if cn is None:
            # Stale node: curriculum_node_id points to a deleted/missing
            # CurriculumNode -> excluded from the current taxonomy entirely.
            continue
        if active_textbook_id is not None and cn.textbook_id != active_textbook_id:
            # Belongs to a different (historical) textbook.
            continue
        result.append(node)
    return result


def generate_knowledge_candidates(
    session: Session,
    *,
    question_body: str,
    standard_solution: str = "",
    solution_steps: list[str] | None = None,
    limit: int = 10,
) -> list[CandidateKnowledgePoint]:
    nodes = current_textbook_taxonomy(session)
    if not nodes:
        return []
    node_ids = [node.id for node in nodes]
    aliases: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for node_id, alias in session.execute(
        select(KnowledgeAlias.node_id, KnowledgeAlias.alias).where(KnowledgeAlias.node_id.in_(node_ids))
    ):
        aliases[node_id].append(alias)
    builtins = {item.name: item for item in CALCULUS_TAXONOMY}
    text = " ".join([question_body, standard_solution, *(solution_steps or [])])
    compact = normalize_name(text)
    query_terms = terms(text)
    candidates: list[CandidateKnowledgePoint] = []
    for node in nodes:
        term_strings = [node.name, *aliases[node.id]]
        item = builtins.get(node.name)
        if item:
            term_strings.extend(item.aliases)
            term_strings.extend(item.keywords)
        # Direct substring: the whole term appears verbatim in the question.
        evidence = list(dict.fromkeys(
            term for term in term_strings
            if term and normalize_name(term) and normalize_name(term) in compact
        ))
        # Token-level recall: a query token (≥2 chars) appearing anywhere inside
        # the KP name also counts as a hit. This lets compound directory KPs
        # such as "参数方程确定函数的二阶导数" be recalled from a question
        # mentioning "参数方程", even when the full name never appears verbatim.
        node_terms: set[str] = set()
        for term in term_strings:
            if term and normalize_name(term):
                node_terms |= terms(term)
        token_hits = query_terms & node_terms
        if not evidence and not token_hits:
            continue
        exact_name = normalize_name(node.name) in compact
        overlap = len(token_hits) / len(query_terms) if query_terms else 0.0
        score = min(0.98, 0.48 + (0.22 if exact_name else 0) + 0.5 * overlap + 0.1 * len(evidence))
        candidates.append(CandidateKnowledgePoint(
            id=node.id,
            name=node.name,
            score=round(score, 2),
            evidence=(evidence[:4] if evidence else sorted(token_hits)[:4]),
        ))
    # Small deterministic family expansion prevents a broad keyword from
    # starving the LLM of specific method candidates (for example, an
    # integral expression should also expose substitution/integration by parts).
    family_markers = (
        (("\\int", "积分", "原函数"), ("积分",)),
        (("\\lim", "极限", "趋于"), ("极限", "洛必达", "无穷小")),
        (("导数", "求导", "可导", "微分"), ("导数", "求导", "微分", "泰勒")),
    )
    existing_ids = {item.id for item in candidates}
    for query_markers, name_markers in family_markers:
        if not any(normalize_name(marker) in compact for marker in query_markers):
            continue
        for node in nodes:
            if node.id in existing_ids or not any(marker in node.name for marker in name_markers):
                continue
            candidates.append(CandidateKnowledgePoint(
                id=node.id,
                name=node.name,
                score=0.35,
                evidence=["同类知识点候选"],
            ))
            existing_ids.add(node.id)
    candidates.sort(key=lambda candidate: (-candidate.score, candidate.name))
    return candidates[: max(5, min(limit, 10))]


def suggest_question_knowledge(session: Session, question: Question, *, limit: int = 10) -> list[dict]:
    solution = question.solution_json or {}
    candidates = generate_knowledge_candidates(
        session,
        question_body=question.question_text or "",
        standard_solution=question.final_answer or "",
        solution_steps=solution.get("solution_steps", []),
        limit=limit,
    )
    return [
        {
            "knowledge_node_id": item.id,
            "name": item.name,
            "score": item.score,
            "evidence": item.evidence,
        }
        for item in candidates
    ]


def _classification_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "submit_knowledge_classification",
            "description": "提交受控知识点分类结果",
            "parameters": KnowledgeClassificationResult.model_json_schema(),
            "strict": True,
        },
    }


def _classification_response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "knowledge_classification",
            "schema": KnowledgeClassificationResult.model_json_schema(),
        },
    }


def _classification_messages(
    question_body: str,
    standard_solution: str,
    solution_steps: list[str],
    candidates: list[CandidateKnowledgePoint],
) -> list[dict]:
    payload = {
        "question_body": question_body,
        "standard_solution": standard_solution,
        "solution_steps": solution_steps,
        "candidate_knowledge_points": [
            {"id": item.id, "name": item.name} for item in candidates
        ],
    }
    return [
        {
            "role": "system",
            "content": (
                "你是高等数学知识点分类器。判断解决题目真正必须使用、且题目主要考查的数学知识，"
                "不是寻找表面关键词。只能从候选列表选择稳定ID，绝不能创造或修改知识点。"
                "主知识点最多1个，辅助知识点最多2个；具体解法优先于宽泛上位概念，例如"
                "洛必达法则优先于函数极限、分部积分法优先于不定积分。无法可靠判断时主知识点为null，"
                "needs_review为true。confidence只是本次判断把握度，不是真实准确率。"
            ),
        },
        {
            "role": "user",
            "content": "请按照指定 JSON Schema 返回分类结果。\n" + json.dumps(payload, ensure_ascii=False),
        },
    ]


def _schema_error_details(error: ValidationError) -> list[dict]:
    details = []
    for item in error.errors(include_url=False, include_context=False, include_input=True):
        field = ".".join(str(part) for part in item.get("loc", ())) or "response"
        error_type = item.get("type", "")
        value = item.get("input")
        if error_type == "missing":
            category = "missing_field"
        elif value is None:
            category = "invalid_null"
        elif field == "confidence" and error_type in {"greater_than_equal", "less_than_equal"}:
            category = "confidence_out_of_range"
        elif field == "secondary_knowledge_point_ids" and error_type == "list_type":
            category = "secondary_not_list"
        else:
            category = "invalid_type"
        details.append({"field": field, "category": category})
    return details


def _decode_json_value(raw: Any, raw_response_type: str) -> Any:
    if raw is None:
        raise StructuredOutputError("empty_response", raw_response_type)
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise StructuredOutputError("schema_validation_error", raw_response_type)
    value = raw.strip()
    if not value:
        raise StructuredOutputError("empty_response", raw_response_type)
    if value.startswith("```") and value.endswith("```"):
        lines = value.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            value = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError as initial_error:
        # Compatibility only: accept a short preface followed by one complete
        # JSON object. Tool arguments and native structured data never use this.
        object_start = value.find("{")
        if object_start <= 0:
            raise StructuredOutputError("json_decode_error", raw_response_type) from initial_error
        try:
            decoded, end = json.JSONDecoder().raw_decode(value[object_start:])
        except json.JSONDecodeError as error:
            raise StructuredOutputError("json_decode_error", raw_response_type) from error
        trailing = value[object_start + end:].strip()
        if trailing not in {"", "```"}:
            raise StructuredOutputError("json_decode_error", raw_response_type) from initial_error
        return decoded


_CONTENT_TOOL_CALL_PREFIX = "<|begin_of_box|>submit_knowledge_classification"
_CONTENT_TOOL_ARGUMENT = re.compile(
    r"<arg_key>(?P<key>[^<]+)</arg_key>\s*"
    r"<arg_value>(?P<value>.*?)"
    r"(?=</arg_value>|<\|end_of_box\|>|</think>|</tool_call>)",
    re.DOTALL,
)


def _decode_content_tool_call(content: str) -> dict:
    """Decode SiliconFlow's documented-in-response tool serialization.

    Some hosted models emit a tool call in content instead of populating the
    OpenAI-compatible tool_calls field. This parser accepts only our exact
    function marker and its arg_key/arg_value protocol.
    """
    value = content.strip()
    if not value.startswith(_CONTENT_TOOL_CALL_PREFIX):
        raise StructuredOutputError("json_decode_error", "content")
    arguments = {
        match.group("key").strip(): match.group("value").strip()
        for match in _CONTENT_TOOL_ARGUMENT.finditer(value)
    }
    expected = {
        "primary_knowledge_point_id",
        "secondary_knowledge_point_ids",
        "confidence",
        "needs_review",
        "reason",
    }
    if not arguments or set(arguments) - expected:
        raise StructuredOutputError("json_decode_error", "content_tool_call")
    primary = arguments.get("primary_knowledge_point_id")
    secondary = arguments.get("secondary_knowledge_point_ids")
    try:
        confidence = json.loads(arguments["confidence"])
        needs_review = json.loads(arguments["needs_review"])
        if secondary is None:
            decoded_secondary = None
        else:
            try:
                decoded_secondary = json.loads(secondary)
            except json.JSONDecodeError:
                # The hosted protocol occasionally emits [uuid] without JSON
                # quotes. Accept only a bracketed comma-separated string list.
                if not (secondary.startswith("[") and secondary.endswith("]")):
                    raise
                body = secondary[1:-1].strip()
                decoded_secondary = [] if not body else [
                    item.strip().strip('"\'') for item in body.split(",")
                ]
    except (KeyError, json.JSONDecodeError) as error:
        raise StructuredOutputError("json_decode_error", "content_tool_call") from error
    return {
        "primary_knowledge_point_id": None if primary == "null" else primary,
        "secondary_knowledge_point_ids": decoded_secondary,
        "confidence": confidence,
        "needs_review": needs_review,
        "reason": arguments.get("reason"),
    }


def _extract_structured_result(response: dict) -> tuple[KnowledgeClassificationResult, str]:
    if not isinstance(response, dict) or not response:
        raise StructuredOutputError("empty_response", "empty")
    if response.get("finish_reason") in {"length", "max_tokens"}:
        raise StructuredOutputError("truncated_response", "response")
    message = response.get("message", response)
    if not isinstance(message, dict):
        raise StructuredOutputError("empty_response", "empty")
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        function = tool_calls[0].get("function") or {}
        raw = function.get("arguments")
        raw_response_type = "tool_calls"
    elif message.get("parsed") is not None:
        raw = message["parsed"]
        raw_response_type = "parsed"
    elif response.get("parsed") is not None:
        raw = response["parsed"]
        raw_response_type = "parsed"
    else:
        raw = message.get("content")
        raw_response_type = "content"
    if (
        raw_response_type == "content"
        and isinstance(raw, str)
        and raw.strip().startswith(_CONTENT_TOOL_CALL_PREFIX)
    ):
        decoded = _decode_content_tool_call(raw)
        raw_response_type = "content_tool_call"
    else:
        decoded = _decode_json_value(raw, raw_response_type)
    try:
        return KnowledgeClassificationResult.model_validate(decoded), raw_response_type
    except ValidationError as error:
        raise StructuredOutputError(
            "schema_validation_error",
            raw_response_type,
            validation_errors=_schema_error_details(error),
        ) from error


def _validate_result(
    session: Session,
    result: KnowledgeClassificationResult,
    candidates: list[CandidateKnowledgePoint],
) -> KnowledgeClassificationResult:
    candidate_ids = {item.id for item in candidates}
    selected_ids = [
        item for item in [result.primary_knowledge_point_id, *result.secondary_knowledge_point_ids]
        if item is not None
    ]
    if len(result.secondary_knowledge_point_ids) > 2:
        raise BusinessValidationError("secondary knowledge points exceed limit")
    if len(set(result.secondary_knowledge_point_ids)) != len(result.secondary_knowledge_point_ids):
        raise BusinessValidationError("secondary knowledge point ids are duplicated")
    if result.primary_knowledge_point_id in result.secondary_knowledge_point_ids:
        raise BusinessValidationError("primary cannot also be secondary")
    if any(node_id not in candidate_ids for node_id in selected_ids):
        raise BusinessValidationError("LLM returned an id outside candidate_knowledge_points")
    legal_ids = {node.id for node in current_textbook_taxonomy(session)}
    if any(node_id not in legal_ids for node_id in selected_ids):
        raise BusinessValidationError("LLM returned a deleted, unapproved, or cross-textbook id")
    if result.confidence < 0.60:
        return result.model_copy(update={"needs_review": True})
    return result


def _fallback_result(candidates: list[CandidateKnowledgePoint], reason: str) -> KnowledgeClassificationResult:
    direct_matches = [item for item in candidates if item.evidence != ["同类知识点候选"]]
    selected = direct_matches[:3]
    return KnowledgeClassificationResult(
        primary_knowledge_point_id=selected[0].id if selected else None,
        secondary_knowledge_point_ids=[item.id for item in selected[1:3]],
        confidence=min(selected[0].score, 0.59) if selected else 0.0,
        needs_review=True,
        reason=reason,
    )


def build_knowledge_llm_backend() -> ChatBackend | None:
    settings = get_settings()
    if not settings.knowledge_llm_enabled or not settings.siliconflow_api_key:
        return None
    return BailianChatBackend(
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        model=settings.siliconflow_agent_model,
        timeout=settings.siliconflow_timeout_seconds,
    )


def classify_text_with_llm(
    session: Session,
    *,
    question_body: str,
    standard_solution: str = "",
    solution_steps: list[str] | None = None,
    backend: ChatBackend | None = None,
    candidate_limit: int = 10,
) -> dict:
    steps = solution_steps or []
    candidates = generate_knowledge_candidates(
        session,
        question_body=question_body,
        standard_solution=standard_solution,
        solution_steps=steps,
        limit=candidate_limit,
    )
    selected_backend = backend or build_knowledge_llm_backend()
    fallback_reason: str | None = None
    raw_response_type: str | None = None
    schema_validation_errors: list[dict] = []
    if not candidates:
        result = _fallback_result([], "规则未召回可供 LLM 判断的候选知识点")
        provenance = "rule_fallback"
        fallback_reason = "no_candidates"
    elif selected_backend is None:
        result = _fallback_result(candidates, "LLM 未配置或未启用，已退回规则候选")
        provenance = "rule_fallback"
        fallback_reason = "llm_unavailable"
    else:
        attempt_count = 0
        try:
            for attempt_count in (1, 2):
                try:
                    response = selected_backend.complete(
                        _classification_messages(question_body, standard_solution, steps, candidates),
                        [],
                        response_format=_classification_response_format(),
                    )
                    structured, raw_response_type = _extract_structured_result(response)
                    break
                except StructuredOutputError:
                    if attempt_count == 2:
                        raise
            result = _validate_result(session, structured, candidates)
            provenance = "llm_suggested"
        except StructuredOutputError as error:
            fallback_reason = error.fallback_reason
            raw_response_type = error.raw_response_type
            schema_validation_errors = error.validation_errors
            result = _fallback_result(candidates, f"LLM 分类失败，已退回规则候选：{fallback_reason}")
            provenance = "rule_fallback"
        except BusinessValidationError:
            fallback_reason = "business_validation_error"
            result = _fallback_result(candidates, "LLM 分类结果未通过业务校验，已退回规则候选")
            provenance = "rule_fallback"
        except (TimeoutError, socket.timeout):
            fallback_reason = "llm_timeout"
            result = _fallback_result(candidates, "LLM 请求超时，已退回规则候选")
            provenance = "rule_fallback"
        except (HTTPError, URLError, OSError):
            fallback_reason = "api_error"
            result = _fallback_result(candidates, "LLM API 不可用，已退回规则候选")
            provenance = "rule_fallback"
        except (KeyError, TypeError, ValueError):
            fallback_reason = "api_error"
            result = _fallback_result(candidates, "LLM 响应格式异常，已退回规则候选")
            provenance = "rule_fallback"
    names = {item.id: item.name for item in candidates}
    return {
        **result.model_dump(),
        "primary_knowledge_point": (
            {"knowledge_id": result.primary_knowledge_point_id, "name": names[result.primary_knowledge_point_id]}
            if result.primary_knowledge_point_id else None
        ),
        "secondary_knowledge_points": [
            {"knowledge_id": node_id, "name": names[node_id]}
            for node_id in result.secondary_knowledge_point_ids
            if node_id in names
        ],
        "candidate_knowledge_points": [item.model_dump() for item in candidates],
        "provenance": provenance,
        "fallback_reason": fallback_reason,
        "llm_raw_response_type": raw_response_type,
        "schema_validation_errors": schema_validation_errors,
        "llm_attempt_count": attempt_count if selected_backend is not None and candidates else 0,
    }


def classify_knowledge_points(
    session: Session,
    question: Question,
    *,
    backend: ChatBackend | None = None,
) -> dict:
    solution = question.solution_json or {}
    result = classify_text_with_llm(
        session,
        question_body=question.question_text or "",
        standard_solution=question.final_answer or "",
        solution_steps=solution.get("solution_steps", []),
        backend=backend,
    )
    result["question_id"] = question.id
    result["knowledge_points"] = [
        {
            "knowledge_id": item["knowledge_id"],
            "name": item["name"],
            "confidence": result["confidence"],
            "role": role,
        }
        for role, items in (
            ("primary", [result["primary_knowledge_point"]] if result["primary_knowledge_point"] else []),
            ("secondary", result["secondary_knowledge_points"]),
        )
        for item in items
    ]
    return result


def confirm_question_knowledge(
    session: Session,
    question_id: str,
    node_ids: list[str],
    *,
    ai_prediction: list[dict] | None = None,
) -> None:
    if not 1 <= len(set(node_ids)) <= 3:
        raise ValueError("知识点必须选择1～3个")
    legal_ids = {node.id for node in current_textbook_taxonomy(session)}
    if any(node_id not in legal_ids for node_id in set(node_ids)):
        raise ValueError("知识点不存在、尚未审核或不属于当前教材")
    previous = set(session.scalars(
        select(QuestionKnowledgeLink.knowledge_node_id).where(
            QuestionKnowledgeLink.question_id == question_id
        )
    ).all())
    final = set(node_ids)
    session.add(QuestionKnowledgeReview(
        question_id=question_id,
        ai_prediction_json=ai_prediction or [],
        human_final_json=list(node_ids),
        deleted_by_human_json=list(previous - final),
        added_by_human_json=list(final - previous),
    ))
    session.execute(delete(QuestionKnowledgeLink).where(
        QuestionKnowledgeLink.question_id == question_id
    ))
    for node_id in dict.fromkeys(node_ids):
        session.add(QuestionKnowledgeLink(
            question_id=question_id,
            knowledge_node_id=node_id,
            relation_type="related",
        ))
    session.flush()
    sync_question_chapter_ownership(session, question_id)

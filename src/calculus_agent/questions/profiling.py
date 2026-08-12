from collections import defaultdict
from datetime import UTC, datetime
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calculus_agent.models import (
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
)
from calculus_agent.question_types import canonical_question_type
from calculus_agent.schemas import (
    QuestionProfileBatchRead,
    QuestionProfileCandidate,
    QuestionProfileRead,
    QuestionProfileUpdate,
)


def profile_approved_questions(
    session: Session,
    *,
    source_name: str | None = "ocr_import",
    force: bool = False,
) -> QuestionProfileBatchRead:
    statement = select(Question).where(
        Question.review_status == "approved", Question.is_active.is_(True)
    )
    if source_name:
        statement = statement.join(QuestionDraft, Question.draft_id == QuestionDraft.id).where(
            QuestionDraft.source_name == source_name
        )
    questions = list(session.scalars(statement).all())
    latest = _latest_profiles(session, [question.id for question in questions])
    knowledge = _knowledge_by_question(session, [question.id for question in questions])
    created = reused = needs_review = 0
    for question in questions:
        if question.id in latest and not force:
            reused += 1
            needs_review += latest[question.id].profile_status == "needs_review"
            continue
        candidate, status = build_profile_candidate(
            question, knowledge.get(question.id, [])
        )
        previous = latest.get(question.id)
        profile = QuestionProfile(
            question_id=question.id,
            profile_version=(previous.profile_version + 1) if previous else 1,
            **candidate.model_dump(exclude={"question_id"}),
            profile_source="auto",
            profile_status=status,
        )
        session.add(profile)
        created += 1
        needs_review += status == "needs_review"
    session.flush()
    return QuestionProfileBatchRead(
        eligible=len(questions), created=created, reused=reused, needs_review=needs_review
    )


def build_profile_candidate(
    question: Question, knowledge_names: list[str]
) -> tuple[QuestionProfileCandidate, str]:
    question_type = canonical_question_type(question.question_type)
    steps = list((question.solution_json or {}).get("solution_steps", []))
    solution = "\n".join(str(step) for step in steps)
    calculation_load = _estimate_calculation_load(question.question_text, solution)

    explicit_steps = max(len(steps), len(re.findall(r"(?:首先|然后|因此|故|从而|令|设)", solution)))
    base_reasoning = 1 if question_type in {"选择题", "填空题"} else 2
    reasoning_depth = _clamp(max(base_reasoning, 1 + min(explicit_steps, 4)))
    if question_type == "证明题":
        reasoning_depth = max(reasoning_depth, 3)

    knowledge_count = len(set(knowledge_names))
    knowledge_depth = _clamp(
        1
        + (reasoning_depth >= 2)
        + (reasoning_depth >= 4)
        + (question_type == "证明题")
        + (knowledge_count >= 3)
    )
    comprehensive_level = _clamp(
        1 + (knowledge_count >= 2) + (knowledge_count >= 3) + (reasoning_depth >= 4)
    )
    difficulty = _clamp(round(
        reasoning_depth * 0.55
        + knowledge_depth * 0.25
        + comprehensive_level * 0.15
        + calculation_load * 0.05
    ))

    # Human-calibrated time standard: use the difficulty band as the
    # authoritative baseline instead of accumulating a formula that
    # systematically overestimates elementary questions.
    estimated_time = {
        1: 2,
        2: 4,   # 3–5 minutes
        3: 7,   # 6–8 minutes
        4: 11,  # 10–12 minutes
        5: 15,
    }[difficulty]
    missing = []
    if not question.final_answer:
        missing.append("答案")
    if not steps:
        missing.append("解析")
    if not knowledge_names:
        missing.append("知识点")
    granularity_flags = _granularity_flags(question.question_text, question.question_type)
    confidence = 0.93
    confidence -= 0.06 if "答案" in missing else 0
    confidence -= 0.25 if "解析" in missing else 0
    confidence -= 0.12 if "知识点" in missing else 0
    confidence -= min(0.28, 0.14 * len(granularity_flags))
    confidence -= 0.2 if question.question_type == "other" else 0
    confidence -= 0.05 if len(steps) == 1 and len(solution) > 500 else 0
    confidence -= 0.03 if calculation_load == 5 else 0
    confidence = round(max(0.35, min(0.95, confidence)), 2)
    status = (
        "needs_review"
        if confidence < 0.65 or "解析" in missing or granularity_flags
        or question.question_type == "other"
        else "pending"
    )
    method_note = (
        f"解析含约{max(1, explicit_steps)}个主要步骤，关联{knowledge_count}个知识点，"
        f"公式与运算密度评为{calculation_load}/5"
    )
    if missing:
        method_note += "；缺少" + "、".join(missing)
    if granularity_flags:
        method_note += "；粒度异常：" + "、".join(granularity_flags)
    return (
        QuestionProfileCandidate(
            question_id=question.id,
            difficulty=difficulty,
            estimated_time_min=estimated_time,
            reasoning_depth=reasoning_depth,
            calculation_load=calculation_load,
            knowledge_depth=knowledge_depth,
            comprehensive_level=comprehensive_level,
            confidence=confidence,
            reason=method_note,
        ),
        status,
    )


def list_question_profiles(
    session: Session,
    *,
    status: str | None = None,
    source_name: str | None = "ocr_import",
) -> list[QuestionProfileRead]:
    questions = list(session.scalars(select(Question)).all())
    by_id = {question.id: question for question in questions}
    latest = _latest_profiles(session, list(by_id))
    knowledge = _knowledge_by_question(session, list(by_id))
    drafts = {
        draft.id: draft for draft in session.scalars(select(QuestionDraft)).all()
    }
    result = []
    for question_id, profile in latest.items():
        question = by_id.get(question_id)
        if question is None or (status and profile.profile_status != status):
            continue
        draft = drafts.get(question.draft_id)
        if source_name and (draft is None or draft.source_name != source_name):
            continue
        result.append(_profile_read(profile, question, knowledge.get(question_id, [])))
    return sorted(result, key=lambda item: (item.profile_status, item.created_at, item.question_id))


def update_question_profile(
    session: Session, profile_id: str, update: QuestionProfileUpdate
) -> QuestionProfileRead:
    current = session.get(QuestionProfile, profile_id)
    if current is None:
        raise LookupError("Question profile not found")
    question = session.get(Question, current.question_id)
    values = {
        field: getattr(update, field) if getattr(update, field) is not None else getattr(current, field)
        for field in (
            "difficulty", "estimated_time_min", "reasoning_depth", "calculation_load",
            "knowledge_depth", "comprehensive_level", "confidence", "reason",
        )
    }
    # Revalidate every human edit before creating an immutable new profile version.
    QuestionProfileCandidate(question_id=question.id, **values)
    version = session.scalar(
        select(func.max(QuestionProfile.profile_version)).where(
            QuestionProfile.question_id == question.id
        )
    ) or current.profile_version
    corrected = QuestionProfile(
        question_id=question.id,
        profile_version=version + 1,
        **values,
        profile_source="corrected" if current.profile_source == "auto" else "human",
        profile_status="approved" if update.approve else "pending",
        reviewed_at=datetime.now(UTC) if update.approve else None,
    )
    session.add(corrected)
    session.flush()
    knowledge = _knowledge_by_question(session, [question.id]).get(question.id, [])
    return _profile_read(corrected, question, knowledge)


def _latest_profiles(session: Session, question_ids: list[str]) -> dict[str, QuestionProfile]:
    if not question_ids:
        return {}
    profiles = session.scalars(
        select(QuestionProfile)
        .where(QuestionProfile.question_id.in_(question_ids))
        .order_by(QuestionProfile.question_id, QuestionProfile.profile_version.desc())
    ).all()
    result = {}
    for profile in profiles:
        result.setdefault(profile.question_id, profile)
    return result


def _knowledge_by_question(session: Session, question_ids: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    if not question_ids:
        return result
    for question_id, name in session.execute(
        select(QuestionKnowledgeLink.question_id, KnowledgeNode.name)
        .join(KnowledgeNode, QuestionKnowledgeLink.knowledge_node_id == KnowledgeNode.id)
        .where(QuestionKnowledgeLink.question_id.in_(question_ids))
    ):
        result[question_id].append(name)
    return result


def _profile_read(
    profile: QuestionProfile, question: Question, knowledge: list[str]
) -> QuestionProfileRead:
    return QuestionProfileRead(
        profile_id=profile.id,
        question_id=profile.question_id,
        question_text=question.question_text,
        question_type=canonical_question_type(question.question_type),
        knowledge=knowledge,
        profile_version=profile.profile_version,
        difficulty=profile.difficulty,
        estimated_time_min=profile.estimated_time_min,
        reasoning_depth=profile.reasoning_depth,
        calculation_load=profile.calculation_load,
        knowledge_depth=profile.knowledge_depth,
        comprehensive_level=profile.comprehensive_level,
        confidence=profile.confidence,
        profile_source=profile.profile_source,
        profile_status=profile.profile_status,
        reason=profile.reason,
        created_at=profile.created_at,
        reviewed_at=profile.reviewed_at,
    )


def _clamp(value: int) -> int:
    return max(1, min(5, int(value)))


_MATH_SEGMENT = re.compile(r"\$\$.*?\$\$|\$.*?\$|\\\[.*?\\\]|\\\(.*?\\\)", re.DOTALL)


def _estimate_calculation_load(question_text: str, solution: str) -> int:
    """Estimate actual transformations, not the number of LaTeX command names."""
    source = solution or question_text
    expressions = []
    for raw in _MATH_SEGMENT.findall(source):
        normalized = re.sub(r"\s+", "", raw)
        if normalized not in expressions:
            expressions.append(normalized)
    math_text = "\n".join(expressions)
    equality_steps = len(re.findall(r"(?<![<>!])=(?!=)", math_text))
    arithmetic = len(re.findall(r"(?<!\\)[+*/]|(?<!\\)-|\\(?:cdot|times|div)", math_text))
    complex_operations = len(re.findall(
        r"\\(?:int|sum|prod|frac\{d|partial|begin\{cases|begin\{aligned)", math_text
    ))
    transformation_words = len(re.findall(
        r"(?:化简|展开|通分|分解|代入|求导|积分|洛必达|换元|取对数)", solution
    ))
    work_units = equality_steps + arithmetic // 4 + complex_operations * 2 + transformation_words
    if work_units <= 2:
        return 1
    if work_units <= 5:
        return 2
    if work_units <= 9:
        return 3
    if work_units <= 15:
        return 4
    return 5


def _granularity_flags(question_text: str, raw_question_type: str) -> list[str]:
    flags = []
    if re.search(r"以下[两二三四五六七八九十]+题", question_text):
        flags.append("题干声明包含多题")
    numbered_parts = re.findall(r"(?:^|[\s（(])(?:\(?[1-9]\d*[)）]|[1-9]\d*[.、])", question_text)
    if len(numbered_parts) >= 2:
        flags.append(f"包含{len(numbered_parts)}个编号小问")
    blanks = len(re.findall(r"_{3,}|\\underline", question_text))
    if canonical_question_type(raw_question_type) == "填空题" and blanks >= 2:
        flags.append(f"包含{blanks}个填空位置")
    return flags

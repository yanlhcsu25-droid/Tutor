import hashlib
from collections import Counter, defaultdict

from ortools.sat.python import cp_model
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calculus_agent.models import KnowledgeNode, Question, QuestionDraft, QuestionKnowledgeLink, QuestionProfile
from calculus_agent.question_types import ALLOWED_QUESTION_TYPES, canonical_question_type
from calculus_agent.schemas import ConstraintCheck, PaperBlueprint, PaperItemRead, PaperPreviewRead
from calculus_agent.agent.schemas import GenerationConstraints, PaperGenerationRequest


# Dataset/demo/test rows remain visible to their respective maintenance flows,
# but are never eligible for a teacher-facing paper.
EXCLUDED_PAPER_SOURCE_NAMES = frozenset({
    "CMM-Math",
    "built-in-demo",
    "test_source",
})


def compose_paper(
    session: Session, blueprint: PaperBlueprint | PaperGenerationRequest
) -> PaperPreviewRead:
    constraints = None
    if isinstance(blueprint, PaperGenerationRequest):
        constraints = blueprint.constraints
        blueprint = blueprint.blueprint
    rows = _candidates(session, blueprint, constraints)
    preferred = set(blueprint.soft_knowledge_preferences)
    rows.sort(key=lambda row: (
        0 if preferred.intersection(row[1]) else 1,
        _seed_key(blueprint.seed, row[0].id), row[0].id,
    ))
    rows_by_id = {row[0].id: row for row in rows}
    required_ids = list(dict.fromkeys([*blueprint.locked_question_ids, *blueprint.manual_question_ids]))
    missing_required = [question_id for question_id in required_ids if question_id not in rows_by_id]
    required = [rows_by_id[question_id] for question_id in required_ids if question_id in rows_by_id]
    selected = None if missing_required else _search(rows, required, blueprint)
    if selected is None:
        # A diagnostic preview may contain eligible supply, but is always marked infeasible.
        eligible = [row for row in rows if _knowledge_allowed(row, blueprint)]
        selected, seen = [], set()
        for row in [*required, *eligible]:
            if row[0].id not in seen and len(selected) < blueprint.total_questions:
                selected.append(row)
                seen.add(row[0].id)
    if blueprint.question_order:
        order = {question_id: index for index, question_id in enumerate(blueprint.question_order)}
        original = {row[0].id: index for index, row in enumerate(selected)}
        selected.sort(key=lambda row: (order.get(row[0].id, len(order)), original[row[0].id]))
    scores = _section_scores(selected, blueprint)
    items = [
        _item(question, knowledge, has_image, score, source_name, source_page)
        for (question, knowledge, has_image, source_name, source_page), score in zip(selected, scores)
    ]
    checks = _checks(blueprint, items, missing_required)
    warnings = [f"未满足约束：{check.name}" for check in checks if not check.satisfied]
    available_knowledge = {name for row in rows for name in row[1]}
    warnings.extend(
        f"目标知识点“{name}”关联不足，将使用相近或未标注题目补足。"
        for name in blueprint.soft_knowledge_preferences
        if name not in available_knowledge
    )
    return PaperPreviewRead(
        title=blueprint.title,
        total_score=sum(item.score for item in items),
        items=items,
        constraints=checks,
        warnings=warnings,
        feasible=all(check.satisfied for check in checks),
    )


def _candidates(
    session: Session,
    blueprint: PaperBlueprint,
    constraints: GenerationConstraints | None = None,
):
    statement = select(Question).join(
        QuestionDraft,
        QuestionDraft.id == Question.draft_id,
    ).where(
        Question.review_status == "approved",
        Question.is_active.is_(True),
        Question.knowledge_match_status == "current",
        QuestionDraft.source_name.not_in(EXCLUDED_PAPER_SOURCE_NAMES),
    )
    if blueprint.excluded_question_ids:
        statement = statement.where(Question.id.not_in(blueprint.excluded_question_ids))
    if constraints and constraints.scope_node_ids:
        statement = statement.join(
            QuestionKnowledgeLink,
            QuestionKnowledgeLink.question_id == Question.id,
        ).where(
            QuestionKnowledgeLink.knowledge_node_id.in_(constraints.scope_node_ids)
        ).distinct()
    if constraints and constraints.allowed_difficulty_levels:
        latest = (
            select(
                QuestionProfile.question_id,
                func.max(QuestionProfile.profile_version).label("profile_version"),
            )
            .where(QuestionProfile.profile_status == "approved")
            .group_by(QuestionProfile.question_id)
            .subquery()
        )
        statement = statement.join(latest, latest.c.question_id == Question.id).join(
            QuestionProfile,
            (QuestionProfile.question_id == latest.c.question_id)
            & (QuestionProfile.profile_version == latest.c.profile_version),
        ).where(QuestionProfile.difficulty.in_(constraints.allowed_difficulty_levels))
    questions = [
        q for q in session.scalars(statement).all()
        if canonical_question_type(q.question_type) in ALLOWED_QUESTION_TYPES
    ]
    if not questions:
        return []
    question_ids = [question.id for question in questions]
    knowledge_by_question: dict[str, list[str]] = defaultdict(list)
    for question_id, name in session.execute(
        select(QuestionKnowledgeLink.question_id, KnowledgeNode.name)
        .join(KnowledgeNode, QuestionKnowledgeLink.knowledge_node_id == KnowledgeNode.id)
        .where(QuestionKnowledgeLink.question_id.in_(question_ids))
    ):
        knowledge_by_question[question_id].append(name)
    draft_ids = [question.draft_id for question in questions]
    draft_by_id = {
        draft.id: draft
        for draft in session.scalars(
            select(QuestionDraft).where(QuestionDraft.id.in_(draft_ids))
        ).all()
    }
    image_by_draft = dict(
        session.execute(
            select(QuestionDraft.id, QuestionDraft.image_path).where(QuestionDraft.id.in_(draft_ids))
        ).all()
    )
    return [
        (
            question,
            knowledge_by_question[question.id],
            bool(image_by_draft.get(question.draft_id)),
            draft_by_id.get(question.draft_id).source_name if draft_by_id.get(question.draft_id) else None,
            _draft_page(draft_by_id.get(question.draft_id)),
        )
        for question in questions
    ]


def _seed_key(seed: int, question_id: str) -> str:
    return hashlib.sha256(f"{seed}:{question_id}".encode()).hexdigest()


def _search(rows, required, blueprint: PaperBlueprint):
    required_ids = {row[0].id for row in required}
    if len(required) > blueprint.total_questions or not all(
        _knowledge_allowed(row, blueprint) for row in required
    ):
        return None
    eligible = [row for row in rows if _knowledge_allowed(row, blueprint)]
    model = cp_model.CpModel()
    selected = [model.new_bool_var(f"q_{index}") for index in range(len(eligible))]
    by_id = {row[0].id: selected[index] for index, row in enumerate(eligible)}
    model.add(sum(selected) == blueprint.total_questions)
    for question_id in required_ids:
        variable = by_id.get(question_id)
        if variable is None:
            return None
        model.add(variable == 1)
    for question_type, count in blueprint.question_type_counts.items():
        model.add(
            sum(
                variable
                for variable, row in zip(selected, eligible)
                if canonical_question_type(row[0].question_type) == question_type
            )
            == count
        )
    if blueprint.question_type_counts:
        allowed_types = set(blueprint.question_type_counts)
        for variable, row in zip(selected, eligible):
            if canonical_question_type(row[0].question_type) not in allowed_types:
                model.add(variable == 0)
    for quota in blueprint.knowledge_quotas:
        model.add(
            sum(variable for variable, row in zip(selected, eligible) if quota.name in row[1])
            >= quota.count
        )
    if blueprint.soft_knowledge_preferences:
        preferred = set(blueprint.soft_knowledge_preferences)
        model.maximize(sum(
            variable for variable, row in zip(selected, eligible)
            if preferred.intersection(row[1])
        ))
    model.add(
        sum(variable for variable, row in zip(selected, eligible) if row[2])
        >= blueprint.image_question_count
    )
    # Candidate variables are created in stable seeded order; single-worker CP-SAT is reproducible.
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = blueprint.seed
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    chosen = [row for variable, row in zip(selected, eligible) if solver.value(variable)]
    required_order = {question_id: index for index, question_id in enumerate(required_ids)}
    type_order = {
        question_type: index for index, question_type in enumerate(blueprint.question_type_counts)
    }
    seeded_order = {row[0].id: index for index, row in enumerate(eligible)}
    chosen.sort(
        key=lambda row: (
            0 if row[0].id in required_order else 1,
            required_order.get(
                row[0].id,
                type_order.get(canonical_question_type(row[0].question_type), len(type_order)),
            ),
            seeded_order[row[0].id],
        )
    )
    return chosen


def _section_scores(selected, blueprint: PaperBlueprint) -> list[int]:
    if blueprint.sections:
        per_type = {item.question_type: item.score_per_question for item in blueprint.sections}
        return [per_type[canonical_question_type(row[0].question_type)] for row in selected]
    return _allocate_scores(
        [row[0].id for row in selected], blueprint.total_score, blueprint.score_overrides
    )


def _knowledge_allowed(row, blueprint: PaperBlueprint) -> bool:
    if set(row[1]).intersection(blueprint.excluded_topics):
        return False
    if not blueprint.strict_knowledge or not blueprint.knowledge_quotas:
        return True
    requested = {quota.name for quota in blueprint.knowledge_quotas}
    return bool(requested.intersection(row[1]))


def _allocate_scores(
    question_ids: list[str],
    total: int,
    overrides: dict[str, float],
) -> list[float]:
    if not question_ids:
        return []
    applicable = {question_id: overrides[question_id] for question_id in question_ids if question_id in overrides}
    flexible = [question_id for question_id in question_ids if question_id not in applicable]
    remaining = total - sum(applicable.values())
    if not flexible:
        return [applicable[question_id] for question_id in question_ids]
    base, remainder = divmod(remaining, len(flexible))
    allocated = {
        question_id: base + (1 if index < remainder else 0)
        for index, question_id in enumerate(flexible)
    }
    return [
        applicable[question_id] if question_id in applicable else allocated[question_id]
        for question_id in question_ids
    ]


def _item(
    question: Question,
    knowledge: list[str],
    has_image: bool,
    score: float,
    source_name: str | None,
    source_page: int | None,
) -> PaperItemRead:
    steps = question.solution_json.get("solution_steps", []) if question.solution_json else []
    return PaperItemRead(
        question_id=question.id,
        question_text=question.question_text,
        question_type=canonical_question_type(question.question_type),
        score=score,
        knowledge=knowledge,
        final_answer=question.final_answer,
        solution_steps=steps,
        has_image=has_image,
        source_name=source_name,
        source_page=source_page,
        review_status=question.review_status,
    )


def _draft_page(draft: QuestionDraft | None) -> int | None:
    # OCR 工作台的页码保存在 QuestionDraft.source_topic（例如“第2页”）。
    if draft is None or not draft.source_topic:
        return None
    match = __import__("re").search(r"(\d+)", draft.source_topic)
    return int(match.group(1)) if match else None


def _checks(
    blueprint: PaperBlueprint,
    items: list[PaperItemRead],
    missing_required: list[str],
) -> list[ConstraintCheck]:
    type_counts = Counter(item.question_type for item in items)
    knowledge_counts = Counter(name for item in items for name in item.knowledge)
    image_count = sum(item.has_image for item in items)
    checks = [
        ConstraintCheck(
            name="题目总数",
            required=blueprint.total_questions,
            actual=len(items),
            satisfied=len(items) == blueprint.total_questions,
        ),
        ConstraintCheck(
            name="试卷总分",
            required=blueprint.total_score,
            actual=sum(item.score for item in items),
            satisfied=len(items) == blueprint.total_questions
            and sum(item.score for item in items) == blueprint.total_score,
        ),
        ConstraintCheck(
            name="指定题目",
            required=len(set(blueprint.locked_question_ids) | set(blueprint.manual_question_ids)),
            actual=len(set(blueprint.locked_question_ids) | set(blueprint.manual_question_ids))
            - len(missing_required),
            satisfied=not missing_required,
        ),
    ]
    for question_type, required in blueprint.question_type_counts.items():
        actual = type_counts[question_type]
        checks.append(
            ConstraintCheck(
                name=f"题型：{question_type}",
                required=required,
                actual=actual,
                satisfied=actual == required,
            )
        )
    for quota in blueprint.knowledge_quotas:
        actual = knowledge_counts[quota.name]
        checks.append(
            ConstraintCheck(
                name=f"知识点：{quota.name}",
                required=quota.count,
                actual=actual,
                satisfied=actual >= quota.count,
            )
        )
    checks.append(
        ConstraintCheck(
            name="图片题数量",
            required=blueprint.image_question_count,
            actual=image_count,
            satisfied=image_count >= blueprint.image_question_count,
        )
    )
    return checks

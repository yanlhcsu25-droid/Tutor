"""Production-shaped deterministic Paper fixture for Teacher Agent evals."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
)

from tests.evals.curriculum_fixture import EVAL_TEXTBOOK_ID
from tests.evals.fixtures.context import EvalFixtureContext


_SECTION_TEMPLATE = (
    "选择题",
    "选择题",
    "选择题",
    "选择题",
    "填空题",
    "填空题",
    "计算题",
    "计算题",
    "计算题",
    "证明题",
)

_DIFFICULTY_TIME = {
    1: 2,
    2: 4,
    3: 7,
    4: 11,
    5: 15,
}


def _chapter_scope(
    session: Session,
    *,
    chapter_code: str = "一",
) -> tuple[CurriculumNode, list[KnowledgeNode]]:
    chapter = session.scalar(
        select(CurriculumNode).where(
            CurriculumNode.textbook_id == EVAL_TEXTBOOK_ID,
            CurriculumNode.node_type == "chapter",
            CurriculumNode.code == chapter_code,
            CurriculumNode.review_status == "approved",
        )
    )
    if chapter is None:
        raise RuntimeError(
            "Eval current_paper fixture requires seed_eval_curriculum() "
            f"to create approved chapter code={chapter_code!r} first."
        )

    knowledge = list(
        session.scalars(
            select(KnowledgeNode)
            .where(
                KnowledgeNode.curriculum_node_id == chapter.id,
                KnowledgeNode.review_status == "approved",
            )
            .order_by(KnowledgeNode.id)
        )
    )
    if not knowledge:
        raise RuntimeError(
            f"Eval chapter {chapter.id} has no approved knowledge nodes."
        )

    return chapter, knowledge


def _section_for_position(position: int) -> str:
    return _SECTION_TEMPLATE[(position - 1) % len(_SECTION_TEMPLATE)]


def _scores(total_score: int, question_count: int) -> list[float]:
    score_each = round(total_score / question_count, 2)
    result = [score_each for _ in range(question_count)]
    result[-1] = round(
        total_score - score_each * (question_count - 1),
        2,
    )
    return result


def _create_question(
    session: Session,
    *,
    key: str,
    question_type: str,
    difficulty: int,
    chapter_id: str,
    knowledge_ids: list[str],
) -> Question:
    draft = QuestionDraft(
        source_name="eval_fixture",
        source_item_id=key,
        variant=1,
        subject="高等数学",
        question_type=question_type,
        question_text=f"Eval fixture question {key}",
        normalized_fingerprint=f"eval-fp-{key}",
        status="approved",
        solution_text=f"Eval fixture solution {key}",
    )
    session.add(draft)
    session.flush()

    question = Question(
        draft_id=draft.id,
        curriculum_chapter_id=chapter_id,
        question_text=draft.question_text,
        question_type=question_type,
        default_score=10,
        final_answer=f"answer-{key}",
        # Generation validation requires the same structured solution shape as
        # production questions.  A plain ``text`` field is not considered a
        # usable solution by validate_paper().
        solution_json={
            "solution_steps": [draft.solution_text],
            "final_answer": f"answer-{key}",
        },
        verification_status="manual_verified",
        review_status="approved",
        is_active=True,
        knowledge_match_status="current",
        publish_source="manual",
    )
    session.add(question)
    session.flush()

    session.add(
        QuestionProfile(
            question_id=question.id,
            profile_version=1,
            difficulty=difficulty,
            estimated_time_min=_DIFFICULTY_TIME[difficulty],
            reasoning_depth=difficulty,
            calculation_load=difficulty,
            knowledge_depth=difficulty,
            comprehensive_level=difficulty,
            confidence=1.0,
            profile_source="eval_fixture",
            profile_status="approved",
            reason="deterministic Teacher Agent eval fixture",
        )
    )

    for knowledge_id in knowledge_ids:
        session.add(
            QuestionKnowledgeLink(
                question_id=question.id,
                knowledge_node_id=knowledge_id,
                relation_type="concept",
                confidence=1.0,
                evidence_json=["eval_fixture"],
            )
        )

    session.flush()
    return question


def seed_success_question_bank(session: Session) -> None:
    """Seed approved, production-shaped candidates for generation evals.

    This is intentionally separate from ``seed_current_paper``: generation
    cases need question supply, while paper mutation cases need a current Paper.
    Chapter three is left unseeded so shortage cases remain deterministic.
    """
    chapter, knowledge = _chapter_scope(session, chapter_code="一")
    knowledge_ids = [node.id for node in knowledge]
    requirements = (
        ("选择题", 4),
        ("填空题", 3),
        ("计算题", 5),
        ("证明题", 2),
    )
    for question_type, count in requirements:
        for index in range(count):
            _create_question(
                session,
                key=f"bank-chapter1-{question_type}-{index}",
                question_type=question_type,
                difficulty=3,
                chapter_id=chapter.id,
                knowledge_ids=knowledge_ids,
            )
    session.flush()


def seed_current_paper(
    session: Session,
    config: dict[str, Any],
) -> EvalFixtureContext:
    """Create a current Paper plus a replacement-capable candidate pool."""

    question_count = config.get("question_count", 10)
    total_score = config.get("total_score", 100)
    chapter_code = str(config.get("chapter_code", "一"))

    if not isinstance(question_count, int) or question_count <= 0:
        raise ValueError(
            "setup.current_paper.question_count must be a positive integer"
        )
    if not isinstance(total_score, int) or total_score <= 0:
        raise ValueError(
            "setup.current_paper.total_score must be a positive integer"
        )

    chapter, knowledge = _chapter_scope(
        session,
        chapter_code=chapter_code,
    )
    knowledge_ids = [node.id for node in knowledge]

    metadata = {
        "scope_chapter_ids": [chapter.id],
        "scope_knowledge_node_ids": knowledge_ids,
        "scope_node_ids": knowledge_ids,
    }

    blueprint = PaperBlueprintRecord(
        title=str(config.get("title", "Eval current paper")),
        blueprint_json={"_agent_metadata": metadata},
        status="used",
    )
    session.add(blueprint)
    session.flush()

    paper = Paper(
        blueprint_id=blueprint.id,
        root_paper_id=None,
        parent_version_id=None,
        version=1,
        status="draft",
        title=str(config.get("title", "Eval current paper")),
        total_score=total_score,
        validation_status="pending",
    )
    session.add(paper)
    session.flush()

    scores = _scores(total_score, question_count)
    paper_types: set[str] = set()

    for position in range(1, question_count + 1):
        question_type = _section_for_position(position)
        paper_types.add(question_type)

        question = _create_question(
            session,
            key=f"paper-{paper.id[:8]}-{position}",
            question_type=question_type,
            difficulty=3,
            chapter_id=chapter.id,
            knowledge_ids=knowledge_ids,
        )
        session.add(
            PaperItem(
                paper_id=paper.id,
                question_id=question.id,
                section=question_type,
                position=position,
                score=scores[position - 1],
                locked=False,
            )
        )

    # Candidate count is an implementation detail.  The contract is only that
    # every present type has easier/same/harder in-scope approved candidates.
    for question_type in sorted(paper_types):
        for difficulty in (2, 3, 4):
            _create_question(
                session,
                key=(
                    f"candidate-{paper.id[:8]}-"
                    f"{question_type}-{difficulty}"
                ),
                question_type=question_type,
                difficulty=difficulty,
                chapter_id=chapter.id,
                knowledge_ids=knowledge_ids,
            )

    session.flush()

    return EvalFixtureContext(
        paper_id=paper.id,
        version_id=paper.id,
    )

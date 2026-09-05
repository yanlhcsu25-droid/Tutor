"""Deterministic regression tests for Eval Repair A."""

from __future__ import annotations

from sqlalchemy import func, select

from calculus_agent.agent.agent import TeacherAgentResult
from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
)
from calculus_agent.agent.schemas import ReplacementIntent
from calculus_agent.agent.tools.replacement_tools import (
    dry_run_replace_question,
)
from calculus_agent.models import (
    Paper,
    PaperItem,
    Question,
    QuestionKnowledgeLink,
    QuestionProfile,
)
from tests.conftest import create_isolated_test_session
from tests.evals.case_loader import EvalCase
from tests.evals.curriculum_fixture import seed_eval_curriculum
from tests.evals.fixtures.paper import seed_current_paper
import tests.evals.runner as eval_runner


def test_current_paper_fixture_builds_real_replacement_capable_graph():
    session = create_isolated_test_session()
    try:
        seed_eval_curriculum(session)

        context = seed_current_paper(
            session,
            {
                "question_count": 10,
                "total_score": 100,
            },
        )

        paper = session.get(Paper, context.paper_id)
        assert paper is not None
        assert context.version_id == paper.id
        assert paper.total_score == 100

        items = list(
            session.scalars(
                select(PaperItem)
                .where(PaperItem.paper_id == paper.id)
                .order_by(PaperItem.position)
            )
        )
        assert len(items) == 10
        assert [item.position for item in items] == list(range(1, 11))
        assert sum(item.score for item in items) == 100

        paper_question_ids = {item.question_id for item in items}

        for question_id in paper_question_ids:
            question = session.get(Question, question_id)
            assert question is not None
            assert question.review_status == "approved"
            assert question.is_active is True
            assert question.knowledge_match_status == "current"
            assert question.curriculum_chapter_id == "eval-chapter-1"

            profile_count = session.scalar(
                select(func.count())
                .select_from(QuestionProfile)
                .where(QuestionProfile.question_id == question_id)
            )
            knowledge_count = session.scalar(
                select(func.count())
                .select_from(QuestionKnowledgeLink)
                .where(QuestionKnowledgeLink.question_id == question_id)
            )
            assert profile_count == 1
            assert knowledge_count >= 1

        outside_candidates = list(
            session.scalars(
                select(Question).where(
                    Question.id.not_in(paper_question_ids),
                    Question.review_status == "approved",
                    Question.is_active.is_(True),
                    Question.knowledge_match_status == "current",
                )
            )
        )
        assert outside_candidates

        preview = dry_run_replace_question(
            session,
            paper_id=paper.id,
            version_id=paper.id,
            intent=ReplacementIntent(
                target_position=3,
                difficulty_direction="easier",
            ),
        )
        assert preview.ok is True
        assert preview.current_question is not None
        assert preview.recommended_question is not None
        assert preview.current_question.difficulty == 3
        assert preview.recommended_question.difficulty < 3
    finally:
        session.close()


def test_generation_fixture_persists_pending_as_source_of_truth():
    session = create_isolated_test_session()
    try:
        seed_eval_curriculum(session)
        conversation_id = "eval-repair-a-generation"

        eval_runner.seed_generation_plan(
            session=session,
            conversation_id=conversation_id,
            plan={
                "paper_type": "chapter_test",
                "scope_names": ["第一章"],
                "total_score": 100,
                "question_type_requirements": [
                    {
                        "question_type": "选择题",
                        "count": 4,
                        "score_each": 5,
                    },
                    {
                        "question_type": "填空题",
                        "count": 2,
                        "score_each": 10,
                    },
                    {
                        "question_type": "计算题",
                        "count": 4,
                        "score_each": 15,
                    },
                ],
            },
        )

        store = DatabasePendingReplacementStore(session)
        pending = store.get_generation(conversation_id)
        memory = store.get_memory(conversation_id)

        assert pending is not None
        assert pending.request.scope_names == ["第一章"]
        assert pending.request.total_score == 100
        assert pending.request.question_count == 10
        assert pending.pending_version == 1
        assert (
            memory.generation_summary
            == pending.request.model_dump(mode="json")
        )

        memory.generation_summary = {
            **memory.generation_summary,
            "total_score": 85,
        }
        store.set_memory(conversation_id, memory)

        loaded = eval_runner.load_pending_generation(
            session,
            conversation_id,
        )
        assert loaded is not None
        assert loaded["total_score"] == 100
        assert loaded["pending_version"] == 1
    finally:
        session.close()


def test_runner_forwards_fixture_paper_ids_to_agent(monkeypatch):
    captured: dict[str, str | None] = {}

    class FakeBackend:
        model = "fixture-test"

    monkeypatch.setattr(
        eval_runner,
        "create_eval_backend",
        lambda case: FakeBackend(),
    )

    def fake_run_teacher_agent(
        session,
        user_message,
        *,
        conversation_id=None,
        paper_id=None,
        version_id=None,
        state_store=None,
        backend=None,
        max_tool_rounds=8,
        trace_recorder=None,
        variant=None,
        tool_fault_injector=None,
        operation_id=None,
    ):
        captured["paper_id"] = paper_id
        captured["version_id"] = version_id
        return TeacherAgentResult(
            status="completed",
            message="fixture forwarding verified",
        )

    monkeypatch.setattr(
        eval_runner,
        "run_teacher_agent",
        fake_run_teacher_agent,
    )

    case = EvalCase(
        id="FIXTURE-CONTEXT-01",
        category="fixture",
        title="current paper ids reach agent",
        setup={
            "current_paper": {
                "question_count": 10,
                "total_score": 100,
            }
        },
        turns=[{"user": "读取当前试卷"}],
        expected={},
        graders=[{"type": "state"}],
    )

    result = eval_runner.run_case(case)

    assert result["error"] is None
    assert captured["paper_id"]
    assert captured["version_id"] == captured["paper_id"]

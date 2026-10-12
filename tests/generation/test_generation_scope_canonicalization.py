from calculus_agent.agent.schemas import GenerationPlanPatch
from calculus_agent.agent.services.generation import GenerationService
from tests.evals.curriculum_fixture import seed_eval_curriculum
from tests.evals.fixtures.paper import seed_success_question_bank


def _preview(session, scope_names):
    seed_eval_curriculum(session)
    seed_success_question_bank(session)
    return GenerationService(
        session=session,
        store=None,
        conversation_id=None,
        user_message="第一章出5题50分，只需要草案。",
    ).preview(GenerationPlanPatch(
        paper_type="chapter_test",
        scope_names=scope_names,
        question_count=5,
        total_score=50,
        question_type_requirements=[
            {"question_type": "选择题", "count": 2},
            {"question_type": "计算题", "count": 3},
        ],
    ))


def test_generation_scope_alias_is_persisted_as_canonical_name(session):
    preview = _preview(session, ["第一章"])

    assert preview.ok
    assert preview.request.scope_names == ["第一章 函数与极限"]


def test_generation_canonical_scope_remains_unchanged(session):
    preview = _preview(session, ["第一章 函数与极限"])

    assert preview.ok
    assert preview.request.scope_names == ["第一章 函数与极限"]


def test_unknown_generation_scope_is_not_rewritten_to_another_chapter(session):
    preview = _preview(session, ["第九章 不存在"])

    assert not preview.ok
    assert preview.request.scope_names == ["第九章 不存在"]
    assert "scope_not_found" in preview.blocking_errors

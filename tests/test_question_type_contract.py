"""题型契约回归测试：「解答题」已废除，unknown 保持 unknown，候选池排除非法题型。

守住三条不可回退的约定：
1. `解答题` 不再是 canonical 题型，且没有任何别名指向它。
2. `unknown` / `subjective` / `other` / `qa` 等不映射为任何正式题型，保持原值。
3. 组卷候选池只接纳 canonical 后落在 ALLOWED_QUESTION_TYPES 的题，
   同时保留对英文原始值（selection / calculation / ...）的兼容。
"""

import pytest

from calculus_agent.agent.tools.paper_tools import build_structured_generation_request
from calculus_agent.api import patch_question_type_value
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
)
from calculus_agent.papers.selector import _candidates, compose_paper
from calculus_agent.question_types import (
    ALLOWED_QUESTION_TYPES,
    PAPER_QUESTION_TYPES,
    QUESTION_TYPE_ALIASES,
    canonical_question_type,
)
from calculus_agent.schemas import PaperBlueprint

# 已废除 / 未定型的题型，一律不得进入正式题型集合。
RETIRED_OR_UNRESOLVED = ("解答题", "问答题", "问答", "解答", "subjective", "qa",
                         "short_answer", "other", "unknown")


def _question(session, number: int, question_type: str, knowledge: KnowledgeNode) -> Question:
    draft = QuestionDraft(
        source_name="type-contract",
        source_item_id=str(number),
        variant=1,
        subject="高等数学",
        grade="大一",
        question_type=question_type,
        question_text=f"第 {number} 题",
        reference_answers_json=[str(number)],
        normalized_fingerprint=str(number).zfill(64),
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        grade=draft.grade,
        question_type=question_type,
        final_answer=str(number),
        solution_json={"solution_steps": [f"解析 {number}"]},
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    session.add(
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge.id,
            relation_type="primary_concept",
        )
    )
    session.flush()
    return question


def _knowledge(session) -> KnowledgeNode:
    node = KnowledgeNode(
        node_type="concept", name="导数", normalized_name="导数", review_status="approved"
    )
    session.add(node)
    session.flush()
    return node


# ---------------------------------------------------------------- 契约层


def test_jieda_is_not_a_canonical_type():
    """「解答题」既不在正式集合，也不是任何别名的目标值。"""
    assert "解答题" not in ALLOWED_QUESTION_TYPES
    assert "解答题" not in PAPER_QUESTION_TYPES
    assert "解答题" not in QUESTION_TYPE_ALIASES.values(), \
        "禁止存在指向「解答题」的别名映射"
    assert "解答题" not in QUESTION_TYPE_ALIASES, \
        "「解答题」不应作为别名 key 被静默接受"


def test_allowed_types_are_exactly_five():
    assert ALLOWED_QUESTION_TYPES == {"选择题", "多选题", "填空题", "计算题", "证明题"}


@pytest.mark.parametrize("raw", RETIRED_OR_UNRESOLVED)
def test_retired_or_unresolved_types_stay_as_is(raw: str):
    """unknown 保持 unknown；废弃/未定型题型不得被映射成任何正式题型。"""
    canonical = canonical_question_type(raw)
    assert canonical == raw, f"{raw} 被意外映射为 {canonical}"
    assert canonical not in ALLOWED_QUESTION_TYPES


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("selection", "选择题"),
        ("single_choice", "选择题"),
        ("单选题", "选择题"),
        ("multiple_choice", "多选题"),
        ("fill_blank", "填空题"),
        ("calculation", "计算题"),
        ("proof", "证明题"),
    ],
)
def test_english_aliases_still_canonicalize(raw: str, expected: str):
    """历史英文原始值必须继续可用，否则会误伤存量题库。"""
    assert canonical_question_type(raw) == expected
    assert canonical_question_type(raw) in ALLOWED_QUESTION_TYPES


# ---------------------------------------------------------------- 候选池层


def test_candidate_pool_excludes_unknown_and_retired_types(session):
    knowledge = _knowledge(session)
    legal = [
        _question(session, 1, "计算题", knowledge),
        _question(session, 2, "calculation", knowledge),   # 英文原始值需保留
        _question(session, 3, "选择题", knowledge),
    ]
    illegal = [
        _question(session, 4, "unknown", knowledge),
        _question(session, 5, "解答题", knowledge),
        _question(session, 6, "subjective", knowledge),
        _question(session, 7, "other", knowledge),
    ]
    session.flush()

    rows = _candidates(session, PaperBlueprint(total_questions=3, question_type_counts={"计算题": 3}))
    pool_ids = {row[0].id for row in rows}

    for question in legal:
        assert question.id in pool_ids, f"合法题型 {question.question_type} 被误排除"
    for question in illegal:
        assert question.id not in pool_ids, f"非法题型 {question.question_type} 泄漏进候选池"


def test_compose_paper_never_selects_unknown(session):
    """即使池里只剩 unknown，也不能被拿去组卷（宁可组不出）。"""
    knowledge = _knowledge(session)
    for number in range(1, 6):
        _question(session, number, "unknown", knowledge)
    session.flush()

    result = compose_paper(
        session,
        PaperBlueprint(
            title="不应成功",
            total_questions=2,
            total_score=20,
            question_type_counts={"计算题": 2},
        ),
    )
    assert not result.items, "unknown 题被选入试卷"


# ---------------------------------------------------------------- 写入层


def test_patch_question_type_rejects_retired_type(session):
    """人工修改入口不得把题目改回「解答题」或任何非法值。"""
    knowledge = _knowledge(session)
    question = _question(session, 1, "unknown", knowledge)
    session.flush()

    for bad in ("解答题", "subjective", "unknown", "问答题"):
        with pytest.raises(ValueError, match="非法的题型"):
            patch_question_type_value(session, question.id, bad)
    assert question.question_type == "unknown", "非法修改不应产生副作用"


def test_patch_question_type_accepts_manual_retype(session):
    """人工定型 unknown -> 计算题后，题目应能进入候选池。"""
    knowledge = _knowledge(session)
    question = _question(session, 1, "unknown", knowledge)
    session.flush()

    rows = _candidates(session, PaperBlueprint(total_questions=1, question_type_counts={"计算题": 1}))
    assert question.id not in {row[0].id for row in rows}

    patch_question_type_value(session, question.id, "计算题")
    session.flush()

    rows = _candidates(session, PaperBlueprint(total_questions=1, question_type_counts={"计算题": 1}))
    assert question.id in {row[0].id for row in rows}
    assert question.question_type == "计算题"
    assert question.review_status == "approved", "人工改题型不应改动审核状态"


def _scope(session) -> None:
    """必须先建好范围，否则 scope 校验会先于题型校验返回 scope_not_found。"""
    chapter = CurriculumNode(
        id="contract-chapter", node_type="chapter", code="3", title="第三章", sort_order=1
    )
    session.add(chapter)
    session.add(
        KnowledgeNode(
            id="contract-knowledge",
            node_type="concept",
            name="第三章知识点",
            normalized_name="第三章知识点",
            curriculum_node_id=chapter.id,
        )
    )
    session.flush()


@pytest.mark.parametrize("bad", ["解答题", "问答题", "subjective", "unknown"])
def test_generation_request_rejects_retired_question_type(session, bad: str):
    """蓝图里出现废弃/未定型题型时必须精确报 question_type_invalid。"""
    from calculus_agent.agent.schemas import GeneratePaperInput, QuestionTypeRequirement

    _scope(session)
    request = GeneratePaperInput(
        paper_type="chapter_test",
        scope_names=["第三章"],
        total_score=100,
        question_type_requirements=[
            QuestionTypeRequirement(question_type=bad, count=5, score_each=20),
        ],
    )
    _, _, errors, _ = build_structured_generation_request(session, request)
    assert errors == ["question_type_invalid"], f"{bad} 未被精确拦截，实际 errors={errors}"


def test_generation_request_accepts_all_allowed_types(session):
    """5 个正式题型必须全部被接受，避免过度收紧。"""
    from calculus_agent.agent.schemas import GeneratePaperInput, QuestionTypeRequirement

    _scope(session)
    request = GeneratePaperInput(
        paper_type="chapter_test",
        scope_names=["第三章"],
        total_score=100,
        question_type_requirements=[
            QuestionTypeRequirement(question_type=name, count=2, score_each=10)
            for name in sorted(ALLOWED_QUESTION_TYPES)
        ],
    )
    _, _, errors, _ = build_structured_generation_request(session, request)
    assert errors == [], f"合法题型被误拦，errors={errors}"

"""题型契约回归测试：唯一合法题型 = {选择题, 填空题, 计算题, 证明题, unknown}。

守住不可回退的约定：
1. canonical 集合只有这五个值；unknown 也是合法 canonical。
2. 历史英文别名（selection / calculation / proof / fill_blank …）与 deprecated
   中文类型（多选题 / 解答题 / 简答题 / 判断题 / 其他 …）都收敛到这五个值，
   无法识别的统一归 unknown。
3. Question / QuestionDraft 的写边界（@validates）强制 canonicalize，
   组卷候选池只接纳 canonical 后落在 ALLOWED_QUESTION_TYPES 的题。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, distinct

from calculus_agent.agent.tools.paper_tools import build_structured_generation_request
from calculus_agent.api import get_session, patch_question_type_value
from calculus_agent.main import app as api_app
from calculus_agent.models import (
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
)
from calculus_agent.papers.selector import _candidates, compose_paper
from calculus_agent.question_types import (
    ALLOWED_QUESTION_TYPES,
    PAPER_QUESTION_TYPES,
    VALID_QUESTION_TYPES,
    canonical_question_type,
)
from calculus_agent.schemas import PaperBlueprint
from scripts.migrate_question_types import execute_migration

_CANONICAL = {"选择题", "填空题", "计算题", "证明题", "unknown"}


# ---------------------------------------------------------------- helpers


def _client(session):
    api_app.dependency_overrides[get_session] = lambda: session
    return TestClient(api_app)


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


def _scope(session) -> None:
    """必须先建好范围，否则 scope 校验会先于题型校验返回 scope_not_found。"""
    from calculus_agent.models import CurriculumNode

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


# ---------------------------------------------------------------- 契约层


def test_contract_has_exactly_five_canonical_types():
    assert VALID_QUESTION_TYPES == _CANONICAL
    assert ALLOWED_QUESTION_TYPES == _CANONICAL
    assert PAPER_QUESTION_TYPES == ("选择题", "填空题", "计算题", "证明题")
    # 多选题 / 解答题 不得作为 canonical 目标残留
    assert "多选题" not in VALID_QUESTION_TYPES
    assert "解答题" not in VALID_QUESTION_TYPES


def test_jieda_is_not_a_canonical_target():
    """「解答题」不是合法题型，也不应作为别名目标值；
    但它可以作为“映射到 unknown”的别名 key 存在（historical compatibility）。"""
    assert "解答题" not in VALID_QUESTION_TYPES
    assert "解答题" not in PAPER_QUESTION_TYPES
    assert "解答题" not in ALLOWED_QUESTION_TYPES
    # canonical_question_type("解答题") 必须收敛到 unknown（不是“解答题”本身）
    assert canonical_question_type("解答题") == "unknown"


@pytest.mark.parametrize("v", ["选择题", "填空题", "计算题", "证明题", "unknown"])
def test_canonical_values_pass_through(v: str):
    assert canonical_question_type(v) == v


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("selection", "选择题"),
        ("choice", "选择题"),
        ("single_choice", "选择题"),
        ("multiple_choice", "选择题"),
        ("多选题", "选择题"),
        ("fill_blank", "填空题"),
        ("blank", "填空题"),
        ("calculation", "计算题"),
        ("calculate", "计算题"),
        ("proof", "证明题"),
        ("proof_question", "证明题"),
        ("Proof", "证明题"),        # 大小写不敏感
        ("CALCULATION", "计算题"),
    ],
)
def test_english_and_case_aliases(raw: str, expected: str):
    assert canonical_question_type(raw) == expected
    assert expected in VALID_QUESTION_TYPES


@pytest.mark.parametrize(
    "raw", ["解答题", "简答题", "判断题", "其他", "subjective", "short_answer", "other", "composite"]
)
def test_deprecated_types_map_to_unknown(raw: str):
    assert canonical_question_type(raw) == "unknown"


@pytest.mark.parametrize("raw", [None, "", "   ", "foobar", "证明题x", "unknownxyz"])
def test_unknown_fallback(raw):
    assert canonical_question_type(raw) == "unknown"


# ---------------------------------------------------------------- 写边界


def test_model_write_boundary_canonicalizes(session):
    """Question / QuestionDraft 构造时即强制 canonicalize（API write 的真实落点）。"""
    k = _knowledge(session)
    q = _question(session, 1, "proof", k)
    d = session.get(QuestionDraft, q.draft_id)
    assert q.question_type == "证明题"
    assert d.question_type == "证明题"
    # 直接赋原始值也会被校验器收敛
    q.question_type = "calculation"
    assert q.question_type == "计算题"


def test_patch_question_type_write_canonicalizes(session):
    """PATCH 入口输入原始 proof，存储为证明题。"""
    k = _knowledge(session)
    q = _question(session, 1, "unknown", k)
    session.flush()
    patch_question_type_value(session, q.id, "proof")
    session.flush()
    assert q.question_type == "证明题"
    assert q.review_status == "approved", "改题型不应改动审核状态"


# ---------------------------------------------------------------- 候选池层


def test_candidate_pool_excludes_unknown(session):
    knowledge = _knowledge(session)
    legal = [
        _question(session, 1, "计算题", knowledge),
        _question(session, 2, "calculation", knowledge),
        _question(session, 3, "选择题", knowledge),
    ]
    illegal = [
        _question(session, 4, "unknown", knowledge),
        _question(session, 5, "解答题", knowledge),   # 收敛为 unknown
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
    knowledge = _knowledge(session)
    for number in range(1, 6):
        _question(session, number, "unknown", knowledge)
    session.flush()
    result = compose_paper(
        session,
        PaperBlueprint(title="不应成功", total_questions=2, total_score=20,
                       question_type_counts={"计算题": 2}),
    )
    assert not result.items, "unknown 题被选入试卷"


# ---------------------------------------------------------------- 生成蓝图层


def test_generation_request_accepts_all_five_allowed_types(session):
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


def test_retired_type_canonicalizes_to_unknown_in_blueprint(session):
    """蓝图里写「解答题」会被收敛为 unknown（合法类型），不再报 question_type_invalid。"""
    from calculus_agent.agent.schemas import GeneratePaperInput, QuestionTypeRequirement

    _scope(session)
    request = GeneratePaperInput(
        paper_type="chapter_test",
        scope_names=["第三章"],
        total_score=100,
        question_type_requirements=[
            QuestionTypeRequirement(question_type="解答题", count=2, score_each=50),
        ],
    )
    _, _, errors, _ = build_structured_generation_request(session, request)
    assert errors == [], f"解答题应被收敛为 unknown 而非报错，errors={errors}"


# ---------------------------------------------------------------- DB migration


def test_migration_rewrites_raw_types_and_keeps_ids(session):
    k = _knowledge(session)
    raw_specs = ["proof", "calculation", "fill_blank", "selection", "解答题", "单选题", "证明题", "计算题", "填空题"]
    questions = [_question(session, i + 1, t, k) for i, t in enumerate(raw_specs)]
    session.flush()
    ids_before = {q.id for q in questions}
    execute_migration(session)
    session.flush()

    stored = {q.id: q.question_type for q in session.scalars(select(Question)).all()}
    ids_after = set(stored)
    assert ids_after == ids_before, "迁移不得改变 question id"
    for q in questions:
        assert stored[q.id] in VALID_QUESTION_TYPES
    # proof/calculation/fill_blank/selection/解答题/单选题 都应被改写
    assert stored[questions[0].id] == "证明题"   # proof
    assert stored[questions[1].id] == "计算题"   # calculation
    assert stored[questions[2].id] == "填空题"   # fill_blank
    assert stored[questions[3].id] == "选择题"   # selection
    assert stored[questions[4].id] == "unknown"  # 解答题
    assert stored[questions[5].id] == "选择题"   # 单选题


def test_migration_is_idempotent(session):
    k = _knowledge(session)
    _questions = [_question(session, i + 1, t, k) for i, t in enumerate(
        ["proof", "calculation", "fill_blank", "selection", "解答题"])]
    session.flush()
    execute_migration(session)
    session.flush()
    first = {q.id: q.question_type for q in session.scalars(select(Question)).all()}
    execute_migration(session)  # 第二次
    session.flush()
    second = {q.id: q.question_type for q in session.scalars(select(Question)).all()}
    assert first == second


def test_all_db_types_legal_after_migration(session):
    k = _knowledge(session)
    for i, t in enumerate(["proof", "calculation", "fill_blank", "selection", "解答题",
                           "单选题", "proof_question", "other", "证明题", "计算题", "填空题"]):
        _question(session, i + 1, t, k)
    session.flush()
    execute_migration(session)
    session.flush()
    distinct_types = set(session.scalars(select(distinct(Question.question_type))).all())
    assert distinct_types <= VALID_QUESTION_TYPES, f"仍存在非 canonical 题型: {distinct_types - VALID_QUESTION_TYPES}"


# ---------------------------------------------------------------- API read / filter


def test_api_read_returns_canonical_even_for_legacy_raw(session):
    k = _knowledge(session)
    q = _question(session, 1, "证明题", k)
    session.flush()
    # 模拟历史脏数据：绕过校验器直接写入原始值
    session.execute(
        Question.__table__.update().where(Question.id == q.id).values(question_type="proof")
    )
    session.flush()
    client = _client(session)
    try:
        resp = client.get(f"/api/v1/questions/{q.id}")
        assert resp.status_code == 200
        assert resp.json()["question_type"] == "证明题"
    finally:
        api_app.dependency_overrides.clear()


def test_api_filter_finds_canonical_and_raw_alias(session):
    k = _knowledge(session)
    q_proof = _question(session, 1, "proof", k)       # 收敛为 证明题
    q_calc = _question(session, 2, "calculation", k)  # 收敛为 计算题
    session.flush()
    client = _client(session)
    try:
        # 规范值过滤
        resp = client.get("/api/v1/questions/search", params={"question_type": "证明题"})
        assert resp.status_code == 200
        items = resp.json()
        ids = {it["id"] for it in items}
        assert q_proof.id in ids
        assert q_calc.id not in ids
        # 原始别名过滤也应命中（入参被 canonicalize）
        resp2 = client.get("/api/v1/questions/search", params={"question_type": "proof"})
        assert {it["id"] for it in resp2.json()} == {q_proof.id}
    finally:
        api_app.dependency_overrides.clear()

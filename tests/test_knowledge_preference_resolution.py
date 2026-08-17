"""知识点偏好解析：区分精确匹配 / orphan 节点 / 跨章节冲突 / 未知。

回归重点：
- 真实 taxonomy 中存在同名 concept 的重复导入节点，旧逻辑会报 knowledge_ambiguous；
  新逻辑按 (name, chapter) 折叠，仅在真正多候选时才报 knowledge_ambiguous。
- curriculum_node_id=None 的 orphan 节点不再无差别通过，
  必须报 knowledge_scope_uncertain，避免"默认任意章节合法"的边界错误。
"""

from pathlib import Path

from calculus_agent.agent.tools.paper_tools import (
    _knowledge_preferences,
    _scope_node_ids,
    generate_paper_from_input,
)
from calculus_agent.agent.schemas import GeneratePaperInput
from calculus_agent.db import build_session_factory
from calculus_agent.models import CurriculumNode, KnowledgeNode


def _seed_synthetic_taxonomy(session) -> None:
    """第三章 + 第一章 + 一个 orphan 节点（无 curriculum_node_id）。"""
    chapter3 = CurriculumNode(
        id="ch3", node_type="chapter", code="三", title="微分中值定理与导数的应用", sort_order=1
    )
    section3 = CurriculumNode(id="sec3", node_type="section", title="导数与微分", parent_id="ch3", sort_order=1)
    chapter1 = CurriculumNode(id="ch1", node_type="chapter", code="一", title="函数与极限", sort_order=2)
    section1 = CurriculumNode(id="sec1", node_type="section", title="极限运算法则", parent_id="ch1", sort_order=1)
    session.add_all([chapter3, section3, chapter1, section1])
    session.add_all([
        KnowledgeNode(
            id="kn-deriv", node_type="concept", name="导数定义",
            normalized_name="导数定义", curriculum_node_id="sec3",
        ),
        KnowledgeNode(
            id="kn-law", node_type="concept", name="极限运算法则",
            normalized_name="极限运算法则", curriculum_node_id="sec1",
        ),
        # orphan: curriculum_node_id=None，系统无法确定性判断章节
        KnowledgeNode(
            id="kn-parent", node_type="knowledge_point", name="函数极限",
            normalized_name="函数极限", curriculum_node_id=None,
        ),
    ])
    session.flush()


def _scope_for(session, labels):
    scope_ids, scope_errors = _scope_node_ids(session, labels)
    assert not scope_errors, scope_errors
    return scope_ids


# Case 1: 合法精确知识点（scope = 知识点真实所属章节）
def test_exact_match_within_scope(session):
    _seed_synthetic_taxonomy(session)
    scope_ids = _scope_for(session, ["第三章"])
    names, ids, errors, questions = _knowledge_preferences(
        session, ["导数定义"], scope_ids, ["第三章"]
    )
    assert errors == []
    assert names == ["导数定义"]
    assert ids == ["kn-deriv"]
    assert questions == []


# Case 2: 跨章节冲突（scope=第三章, knowledge 属于第一章）
def test_cross_chapter_conflict(session):
    _seed_synthetic_taxonomy(session)
    scope_ids = _scope_for(session, ["第三章"])
    names, ids, errors, questions = _knowledge_preferences(
        session, ["极限运算法则"], scope_ids, ["第三章"]
    )
    assert names == [] and ids == []
    assert errors == ["knowledge_scope_conflict"]
    assert len(questions) == 1
    assert "极限运算法则" in questions[0]
    assert "不属于" in questions[0] or "不在当前章节" in questions[0]


# Case 3: 完全不存在的知识点
def test_unknown_knowledge(session):
    _seed_synthetic_taxonomy(session)
    scope_ids = _scope_for(session, ["第三章"])
    names, ids, errors, questions = _knowledge_preferences(
        session, ["量子力学"], scope_ids, ["第三章"]
    )
    assert names == [] and ids == []
    assert errors == ["knowledge_unknown"]
    assert len(questions) == 1
    assert "量子力学" in questions[0]


# Case 4: orphan 节点（curriculum_node_id=None）必须报 knowledge_scope_uncertain
def test_orphan_node_is_scope_uncertain(session):
    _seed_synthetic_taxonomy(session)
    scope_ids = _scope_for(session, ["第三章"])
    names, ids, errors, questions = _knowledge_preferences(
        session, ["函数极限"], scope_ids, ["第三章"]
    )
    assert names == [] and ids == []
    assert errors == ["knowledge_scope_uncertain"]
    assert len(questions) == 1
    assert "函数极限" in questions[0]
    # 即使放到同一章节 scope 下也不能默认通过
    scope_ids_in_chapter_one = _scope_for(session, ["第一章"])
    names2, ids2, errors2, _ = _knowledge_preferences(
        session, ["函数极限"], scope_ids_in_chapter_one, ["第一章"]
    )
    assert names2 == [] and ids2 == []
    assert errors2 == ["knowledge_scope_uncertain"]


# 回归：同名重复导入节点不应误报 knowledge_ambiguous
def test_duplicate_nodes_collapsed(session):
    _seed_synthetic_taxonomy(session)
    # 模拟一次重复导入：同一名称 + 同一章节但不同 node_type（合成库有唯一约束，
    # 真实库中存在同名 concept 重复，这里用不同 node_type 复现折叠路径）
    session.add(KnowledgeNode(
        id="kn-law-dup", node_type="knowledge_point", name="极限运算法则",
        normalized_name="极限运算法则", curriculum_node_id="sec1",
    ))
    session.flush()
    scope_ids = _scope_for(session, ["第三章"])
    names, ids, errors, questions = _knowledge_preferences(
        session, ["极限运算法则"], scope_ids, ["第三章"]
    )
    assert "knowledge_ambiguous" not in errors
    assert errors == ["knowledge_scope_conflict"]


# 集成：澄清文案经 generate_paper_from_input 透传为 clarification_questions
def test_clarification_propagates_to_tool_result(session):
    _seed_synthetic_taxonomy(session)
    request = GeneratePaperInput(
        paper_type="chapter_exercise",
        scope_names=["第三章"],
        knowledge_preferences=["极限运算法则"],
    )
    result = generate_paper_from_input(session, request)
    assert result.ok is False
    assert "knowledge_scope_conflict" in result.blocking_errors
    assert result.needs_clarification is True
    assert result.clarification_questions
    assert "极限运算法则" in result.clarification_questions[0]


# ---- orphaned KnowledgeNode 与当前 taxonomy 的区分（regression） ----

def test_orphaned_and_valid_same_name_resolves_to_scope_conflict(session):
    """旧 orphaned KN（curriculum_node_id 指向已删除节点）必须被 resolver 排除，
    只剩当前有效 KN 参与匹配 → 跨章节报 knowledge_scope_conflict，而非 ambiguous。"""
    _seed_synthetic_taxonomy(session)
    # 旧 orphaned 节点：curriculum_node_id 指向不存在的 CurriculumNode。
    # normalized_name 加后缀以绕过 (node_type, normalized_name) 唯一约束；
    # resolver 按 normalize_name(name) 匹配，name 仍为“极限运算法则”。
    session.add(KnowledgeNode(
        id="kn-law-orphan", node_type="concept", name="极限运算法则",
        normalized_name="极限运算法则::deleted-cn-id", curriculum_node_id="deleted-cn-id",
    ))
    session.flush()
    scope_ids = _scope_for(session, ["第三章"])
    names, ids, errors, questions = _knowledge_preferences(
        session, ["极限运算法则"], scope_ids, ["第三章"]
    )
    assert "knowledge_ambiguous" not in errors
    assert errors == ["knowledge_scope_conflict"]
    # 若孤儿 KN 未被排除，会与有效 KN 同名 → 误报 knowledge_ambiguous；
    # 此处得到 knowledge_scope_conflict 证明孤儿已被排除、只匹配到唯一有效 KN。


def test_two_valid_same_name_nodes_still_ambiguous(session):
    """两个当前有效、同名的 KN（分属不同章节）仍应报 knowledge_ambiguous。
    证明本次修复没有误删 ambiguity 语义。"""
    _seed_synthetic_taxonomy(session)
    session.add_all([
        KnowledgeNode(
            id="kn-dup-a", node_type="concept", name="同名导数",
            normalized_name="同名导数::sec1", curriculum_node_id="sec1",
        ),
        KnowledgeNode(
            id="kn-dup-b", node_type="concept", name="同名导数",
            normalized_name="同名导数::sec3", curriculum_node_id="sec3",
        ),
    ])
    session.flush()
    # scope 同时覆盖两个章节，二者都在 scope 内 → 无法唯一确定
    scope_ids = _scope_for(session, ["第一章", "第三章"])
    names, ids, errors, questions = _knowledge_preferences(
        session, ["同名导数"], scope_ids, ["第一章", "第三章"]
    )
    assert errors == ["knowledge_ambiguous"]
    assert len(questions) == 1


def test_only_orphaned_knowledge_is_unknown(session):
    """若某名字只有 orphaned KN（无当前有效 KN），不应被当成正常知识点，
    而应判 knowledge_unknown（孤儿节点已不在当前 taxonomy 内）。"""
    _seed_synthetic_taxonomy(session)
    session.add(KnowledgeNode(
        id="kn-ghost", node_type="concept", name="孤儿子节点",
        normalized_name="孤儿子节点", curriculum_node_id="ghost-cn-id",
    ))
    session.flush()
    scope_ids = _scope_for(session, ["第三章"])
    names, ids, errors, questions = _knowledge_preferences(
        session, ["孤儿子节点"], scope_ids, ["第三章"]
    )
    assert names == [] and ids == []
    assert errors == ["knowledge_unknown"]
    assert "孤儿子节点" in questions[0]


# ---- 真实 calculus_agent.db 回归 ----

def test_real_bad_case_resolver_returns_three_distinct_errors():
    """原始 bad case：第三章 + [函数极限, 极限运算法则, 无穷小]。

    真实 taxonomy 现状：
      - 函数极限  : knowledge_point，curriculum_node_id=None → knowledge_scope_uncertain
      - 极限运算法则: concept，curriculum_node_id 属于第一章 → knowledge_scope_conflict
      - 无穷小     : 无匹配 → knowledge_unknown
    """
    real_db = Path(__file__).resolve().parents[1] / "calculus_agent.db"
    if not real_db.exists():
        import pytest
        pytest.skip("真实 calculus_agent.db 不存在，跳过真实数据回归测试")
    session = build_session_factory(f"sqlite:///{real_db}")()
    scope_ids, scope_errors = _scope_node_ids(session, ["第三章"])
    assert not scope_errors

    names, ids, errors, questions = _knowledge_preferences(
        session, ["函数极限", "极限运算法则", "无穷小"], scope_ids, ["第三章"]
    )
    assert names == [] and ids == []
    assert "knowledge_ambiguous" not in errors
    # 三种业务澄清状态都出现，且顺序按输入顺序
    assert errors == [
        "knowledge_scope_uncertain",
        "knowledge_scope_conflict",
        "knowledge_unknown",
    ], errors
    assert len(questions) == 3
    joined = " ".join(questions)
    for term in ("函数极限", "极限运算法则", "无穷小"):
        assert term in joined, (term, questions)


def test_real_db_first_chapter_is_in_scope():
    """第一章 + 极限运算法则 必须在真实 taxonomy 下解析为 resolved（避免硬编码）。"""
    real_db = Path(__file__).resolve().parents[1] / "calculus_agent.db"
    if not real_db.exists():
        import pytest
        pytest.skip("真实 calculus_agent.db 不存在，跳过真实数据回归测试")
    session = build_session_factory(f"sqlite:///{real_db}")()
    scope_ids, _ = _scope_node_ids(session, ["第一章"])
    names, ids, errors, questions = _knowledge_preferences(
        session, ["极限运算法则"], scope_ids, ["第一章"]
    )
    assert errors == []
    assert "极限运算法则" in names
    assert ids
    assert questions == []
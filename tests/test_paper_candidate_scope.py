"""组卷候选题查询的章节范围测试（最小红测，定位 papers/selector.py:_candidates）。

用户通过真实页面证明：第一章试卷会抽到第三章内容。
沿链："生成第一章试卷" → scope="第一章" → _scope_node_ids → scope_node_ids (第一章 KN 集合)
     → compose_paper → _candidates → QuestionKnowledgeLink.knowledge_node_id.in_(scope_node_ids)
     → 候选池 → 组卷算法 → 试卷。

本测试卡在候选池层（_candidates），不卡最终随机抽题：
  题甲：只含第一章知识点          → 应在候选池
  题乙：第一章 + 第三章知识点     → 不应在候选池（派生章节=第三章）

当前 papers/selector.py:96-102 用 .in_(scope_node_ids) + .distinct()，语义是
「任意一个 KP 命中目标章节知识点就算候选」——题乙含 ch1 KP，IN 命中，错误进入
第一章候选池。这与筛选侧之前已修复的 "派生章节而非后代匹配" 同一类不变量缺失。
"""
from __future__ import annotations

import uuid


from calculus_agent.agent.schemas import GenerationConstraints
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    Textbook,
)
from calculus_agent.papers.selector import _candidates
from calculus_agent.questions.chapter_assignment import (
    sync_question_chapter_ownership,
)
from calculus_agent.schemas import PaperBlueprint


def _seed_two_chapters(session) -> tuple[Textbook, dict[str, CurriculumNode], dict[str, KnowledgeNode]]:
    """建第一章 + 第三章目录 + 每章一个已审核 KN。"""
    book = Textbook(name="测试教材", is_active=True)
    session.add(book)
    session.flush()

    chapters: dict[str, CurriculumNode] = {}
    kns: dict[str, KnowledgeNode] = {}
    for key, title, order in [
        ("ch1", "函数与极限", 10),
        ("ch3", "微分中值定理", 30),
    ]:
        ch = CurriculumNode(
            textbook_id=book.id, node_type="chapter", title=title,
            sort_order=order, review_status="approved",
        )
        session.add(ch)
        session.flush()
        sec = CurriculumNode(
            textbook_id=book.id, parent_id=ch.id, node_type="section",
            title=f"{title}-节", sort_order=order + 1, review_status="approved",
        )
        session.add(sec)
        session.flush()
        kn = KnowledgeNode(
            curriculum_node_id=sec.id, node_type="concept",
            name=f"{title}-KP", normalized_name=f"{title}-KP",
            source_type="textbook_directory", review_status="approved",
        )
        session.add(kn)
        session.flush()
        chapters[key] = ch
        kns[key] = kn
    return book, chapters, kns


def _make_question(session, kn_ids: list[str]) -> Question:
    """造一道 approved/current/active 的题，关联给定 KP（question_type=计算题，可组卷）。"""
    draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id=str(uuid.uuid4()),
        variant=1, subject="高等数学", question_type="calculation",
        question_text="题干", solution_text="解",
        normalized_fingerprint="f" * 64, status="approved",
    )
    session.add(draft)
    session.flush()
    q = Question(
        draft_id=draft.id, question_text="题干", question_type="calculation",
        solution_json={"solution_steps": []},
        verification_status="manual_verified", review_status="approved",
        is_active=True, knowledge_match_status="current",
    )
    session.add(q)
    session.flush()
    for kn_id in kn_ids:
        session.add(QuestionKnowledgeLink(
            question_id=q.id, knowledge_node_id=kn_id,
            relation_type="related", confidence=1.0,
            evidence_json=[{"source": "test"}],
        ))
    session.flush()
    sync_question_chapter_ownership(session, q.id)
    return q


def test_chapter_one_candidate_pool_excludes_cross_chapter_question(session) -> None:
    """生成第一章试卷的候选池应只含「派生章节=第一章」的题，不应含跨章题。

    题甲：只含第一章 KP        → 期望在候选池
    题乙：第一章 + 第三章 KP   → 期望不在候选池（派生到第三章）
    """
    _, chapters, kns = _seed_two_chapters(session)
    q_ch1_only = _make_question(session, [kns["ch1"].id])
    q_cross = _make_question(session, [kns["ch1"].id, kns["ch3"].id])

    constraints = GenerationConstraints(
        scope=["第一章"],
        scope_chapter_ids=[chapters["ch1"].id],
        scope_node_ids=[kns["ch1"].id],
    )
    blueprint = PaperBlueprint(
        title="第一章测试卷",
        total_questions=2,
        total_score=20,
        question_type_counts={"计算题": 2},
    )

    rows = _candidates(session, blueprint, constraints)
    candidate_ids = {row[0].id for row in rows}

    # 题甲 → 在候选池
    assert q_ch1_only.id in candidate_ids, (
        f"只含第一章 KP 的题应进入第一章候选池，实际候选={[r[0].id for r in rows]}"
    )
    # 题乙 → 不在候选池（跨章派生到第三章）
    assert q_cross.id not in candidate_ids, (
        f"跨章题（ch1+ch3）派生章节=第三章，不应进入第一章候选池，"
        f"实际候选={[r[0].id for r in rows]}"
    )

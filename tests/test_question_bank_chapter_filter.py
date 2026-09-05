"""题库「按大章节筛选」确定性逻辑测试（覆盖需求 Case 1-7）。

数据链路：OcrImportDraft.knowledge_points_json → KnowledgeNode → CurriculumNode 父链 → 一级章节。
全部为确定性计算，不依赖 LLM。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from fastapi.testclient import TestClient

from calculus_agent.api import import_textbook_directory
from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import (
    KnowledgeNode,
    OcrImportDraft,
    OcrImportSource,
    Textbook,
)
from calculus_agent.workbench.chapter_filter import (
    chapter_display_label,
    filter_questions_by_chapter,
    list_top_level_chapters,
)
from calculus_agent.workbench.database import WorkbenchDatabase

DIRECTORY_TEXT = """
第一章 函数与极限
1.1 映射与函数
1.2 函数的极限
第二章 导数与微分
2.1 导数定义
第三章 微分中值定理
3.1 洛必达法则
"""

# 合法来源 id：`src_` + 32 位 hex（与 workbench app 的 _safe_id 校验一致）
SOURCE_ID = f"src_{uuid.uuid4().hex}"


def _knowledge_id(session, name: str) -> str:
    return session.scalar(select(KnowledgeNode.id).where(KnowledgeNode.name == name))


def _seed(session) -> dict:
    """构造激活教材 + 3 个一级章节 + 若干已发布题目（含跨章 / 多知识点 / 无知识点）。"""
    book = Textbook(name="测试教材", is_active=True)
    session.add(book)
    session.flush()
    import_textbook_directory(book.id, {"text": DIRECTORY_TEXT, "replace": True}, session)

    kp = {
        "ch1": _knowledge_id(session, "函数与极限"),
        "s1a": _knowledge_id(session, "映射与函数"),
        "s1b": _knowledge_id(session, "函数的极限"),
        "ch2": _knowledge_id(session, "导数与微分"),
        "s2": _knowledge_id(session, "导数定义"),
        "ch3": _knowledge_id(session, "微分中值定理"),
        "s3": _knowledge_id(session, "洛必达法则"),
    }

    source = OcrImportSource(
        id=SOURCE_ID, original_name="t.pdf", stored_path="/tmp/t.pdf",
        sha256="seed", layout_json={},
    )
    session.add(source)
    session.flush()

    def draft(qid: str, points: list[str], *, published: bool = True) -> None:
        session.add(OcrImportDraft(
            id=qid, source_id=SOURCE_ID, page_number=1, original_number=qid,
            ocr_markdown="", edited_markdown="",
            review_status="published" if published else "reviewed",
            knowledge_points_json=list(points),
        ))

    # Q1 第一章子节点；Q2 第二章子节点；Q3 跨第一/二章；
    # Q4 两个第一章知识点（去重）；Q5 第三章；Q6 无知识点；Q7 第一章但非发布。
    draft("Q1", [kp["s1a"]])
    draft("Q2", [kp["s2"]])
    draft("Q3", [kp["s1b"], kp["s2"]])
    draft("Q4", [kp["s1a"], kp["s1b"]])
    draft("Q5", [kp["s3"]])
    draft("Q6", [])
    draft("Q7", [kp["s1a"]], published=False)
    session.flush()

    chapters = {c.title: c for c in list_top_level_chapters(session)}
    return {"kp": kp, "chapters": chapters}


def _load(session) -> list[dict]:
    return WorkbenchDatabase(session).list_questions(SOURCE_ID)


# ── 章节列表 / 标签 ──

def test_list_top_level_chapters_returns_active_textbook_chapters(session) -> None:
    _seed(session)
    chapters = list_top_level_chapters(session)
    titles = {c.title for c in chapters}
    assert {"函数与极限", "导数与微分", "微分中值定理"} <= titles
    labels = {chapter_display_label(c) for c in chapters}
    assert "第一章 函数与极限" in labels
    assert "第二章 导数与微分" in labels


# ── Case 1：chapter_id=None 返回全部题目 ──

def test_no_chapter_filter_returns_all(session) -> None:
    _seed(session)
    items = _load(session)
    assert {i["question_id"] for i in items} == {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"}


# ── Case 2：知识点属于第一章子节点，筛选第一章应返回 ──

def test_filter_by_chapter_one_returns_child_knowledge_question(session) -> None:
    ctx = _seed(session)
    ch1 = ctx["chapters"]["函数与极限"]
    items = filter_questions_by_chapter(session, _load(session), ch1.id)
    ids = {i["question_id"] for i in items}
    assert "Q1" in ids  # 映射与函数（第一章子节点）


# ── Case 3：属于第二章的题目，筛选第一章不应返回 ──

def test_filter_by_chapter_one_excludes_other_chapter(session) -> None:
    ctx = _seed(session)
    ch1 = ctx["chapters"]["函数与极限"]
    items = filter_questions_by_chapter(session, _load(session), ch1.id)
    ids = {i["question_id"] for i in items}
    assert "Q2" not in ids  # 导数定义（第二章子节点）


# ── Case 4：绑定多个第一章知识点，结果只出现一次 ──

def test_filter_dedup_multiple_knowledge_points_same_chapter(session) -> None:
    ctx = _seed(session)
    ch1 = ctx["chapters"]["函数与极限"]
    items = filter_questions_by_chapter(session, _load(session), ch1.id)
    q4 = [i for i in items if i["question_id"] == "Q4"]
    assert len(q4) == 1


# ── Case 5：跨第一/二章的题目，派生到最靠后的第二章，只在第二章筛选出现 ──

def test_filter_cross_chapter_question_appears_only_in_latest(session) -> None:
    ctx = _seed(session)
    ch1 = ctx["chapters"]["函数与极限"]
    ch2 = ctx["chapters"]["导数与微分"]
    ids1 = {i["question_id"] for i in filter_questions_by_chapter(session, _load(session), ch1.id)}
    ids2 = {i["question_id"] for i in filter_questions_by_chapter(session, _load(session), ch2.id)}
    assert "Q3" not in ids1  # 跨章派生到第二章，不出现在第一章筛选
    assert "Q3" in ids2


# ── Case 5b：跨第一章 + 第三章的题目，派生到第三章，只在第三章筛选出现 ──

def test_filter_cross_ch1_ch3_question_appears_only_in_ch3(session) -> None:
    ctx = _seed(session)
    kp = ctx["kp"]
    ch1 = ctx["chapters"]["函数与极限"]
    ch3 = ctx["chapters"]["微分中值定理"]
    session.add(OcrImportDraft(
        id="Q8", source_id=SOURCE_ID, page_number=1, original_number="Q8",
        ocr_markdown="", edited_markdown="",
        review_status="published",
        knowledge_points_json=[kp["s1a"], kp["s3"]],
    ))
    session.flush()
    ids1 = {i["question_id"] for i in filter_questions_by_chapter(session, _load(session), ch1.id)}
    ids3 = {i["question_id"] for i in filter_questions_by_chapter(session, _load(session), ch3.id)}
    assert "Q8" not in ids1  # 筛选第一章：不能出现
    assert "Q8" in ids3      # 筛选第三章：必须出现


# ── Case 6：章节筛选 + 已发布筛选 = AND，非 OR ──

def test_chapter_filter_combined_with_published_is_and(session) -> None:
    ctx = _seed(session)
    ch1 = ctx["chapters"]["函数与极限"]
    items = filter_questions_by_chapter(session, _load(session), ch1.id)
    # 前端在 API 结果之上再叠加「仅已发布」过滤；两者为 AND。
    published = [i for i in items if i["review_status"] == "published"]
    ids = {i["question_id"] for i in published}
    assert ids == {"Q1", "Q4"}  # 第一章且已发布；Q3（跨章→第二章）、Q7（reviewed）被排除
    assert "Q3" not in ids
    assert "Q7" not in ids


# ── Case 7：切回「全部章节」恢复无章节限制行为 ──

def test_switch_back_to_all_restores_unfiltered(session) -> None:
    ctx = _seed(session)
    ch1 = ctx["chapters"]["函数与极限"]
    filtered = filter_questions_by_chapter(session, _load(session), ch1.id)
    full = _load(session)
    # 切回全部章节 = 不再应用章节过滤，恢复完整列表
    assert len(full) == 7
    assert len(filtered) < len(full)
    assert {i["question_id"] for i in full} >= {i["question_id"] for i in filtered}


# ── 集成：真实端点（chapter_id 查询参数 + chapters 列表）──

def test_endpoint_chapter_filter_and_chapters_list(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 't.db'}"
    create_schema(db_url)
    factory = build_session_factory(db_url)
    with factory.begin() as s:
        _seed(s)
        list_top_level_chapters(s)[0].id  # 确认第一章可读取（sort_order 最小）

    import calculus_agent.workbench.app as app_module
    old_factory = app_module._session_factory
    app_module._session_factory = factory
    try:
        client = TestClient(app_module.app)
        chapters_resp = client.get("/api/taxonomy/chapters")
        assert chapters_resp.status_code == 200
        chapters = chapters_resp.json()["items"]
        assert any(c["name"].startswith("第一章") for c in chapters)
        target = next(c for c in chapters if c["name"].startswith("第一章"))

        # 带 chapter_id：仅返回第一章题目
        resp = client.get(f"/api/sources/{SOURCE_ID}/questions?chapter_id={target['id']}")
        assert resp.status_code == 200
        filtered_ids = {q["question_id"] for q in resp.json()["items"]}
        assert "Q1" in filtered_ids
        assert "Q2" not in filtered_ids

        # 不带 chapter_id：返回全部
        resp_all = client.get(f"/api/sources/{SOURCE_ID}/questions")
        assert resp_all.status_code == 200
        assert len(resp_all.json()["items"]) == 7
    finally:
        app_module._session_factory = old_factory

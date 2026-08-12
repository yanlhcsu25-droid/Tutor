"""发布封板回归测试。

验证状态机单向约束：pending → in_review → reviewed → published，
普通审核保存（PATCH /api/questions/{id}）不得把已发布的草稿回退为 in_review，
且已发布后其对应正式 Question 内容不可被普通保存修改。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import (
    OcrImportSource,
    OcrImportDraft,
    QuestionDraft,
    Question,
)
import calculus_agent.workbench.app as wb_app

SOURCE_ID = "src_00000000000000000000000000000001"
DRAFT_ID = "q_00000000000000000000000000000001"

MARKDOWN = """## 题目内容

求极限 $\\lim_{x\\to0} \\frac{\\sin x}{x}$

## 参考解答

由重要极限得结果为 1。

## 题型

calculation

## 知识点

函数的极限

## 难度

3

## 来源页码

1

## 原始题号

1
"""


@pytest.fixture
def client_and_factory(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'wb_freeze_test.db'}"
    create_schema(db_url)
    factory = build_session_factory(db_url)
    with factory.begin() as s:
        s.add(
            OcrImportSource(
                id=SOURCE_ID,
                original_name="t.pdf",
                stored_path="/tmp/t.pdf",
                sha256="x" * 64,
                page_count=1,
                processing_status="done",
            )
        )
        s.add(
            OcrImportDraft(
                id=DRAFT_ID,
                source_id=SOURCE_ID,
                page_number=1,
                original_number="1",
                ocr_markdown=MARKDOWN,
                edited_markdown=MARKDOWN,
                review_status="pending",
            )
        )
    # 用测试库替换 workbench app 的会话工厂，确保请求落到同一数据库
    import calculus_agent.workbench.app as app_mod

    saved = app_mod._session_factory
    app_mod._session_factory = factory
    try:
        yield TestClient(app_mod.app), factory
    finally:
        app_mod._session_factory = saved


def _find_question(factory):
    with factory.begin() as s:
        qd = s.scalar(
            select(QuestionDraft).where(
                QuestionDraft.source_item_id == DRAFT_ID,
                QuestionDraft.source_name == "ocr_import",
            )
        )
        return s.scalar(select(Question).where(Question.draft_id == qd.id))


def test_save_pending_question(client_and_factory):
    """pending 草稿可以保存，状态进入 in_review。"""
    client, _ = client_and_factory
    r = client.patch(f"/api/questions/{DRAFT_ID}", json={"markdown": MARKDOWN})
    assert r.status_code == 200
    assert r.json()["question"]["review_status"] == "in_review"


def test_save_in_review_question(client_and_factory):
    """in_review 草稿可以继续保存（不会被封板拦截）。"""
    client, _ = client_and_factory
    client.patch(f"/api/questions/{DRAFT_ID}", json={"markdown": MARKDOWN})
    r = client.patch(
        f"/api/questions/{DRAFT_ID}", json={"markdown": MARKDOWN + "\n"}
    )
    assert r.status_code == 200
    assert r.json()["question"]["review_status"] == "in_review"


def test_save_published_question(client_and_factory):
    """已发布草稿再次保存必须返回 409，且内容与状态均不变。"""
    client, factory = client_and_factory

    # 1) 保存审核 → in_review
    client.patch(f"/api/questions/{DRAFT_ID}", json={"markdown": MARKDOWN})
    # 2) 发布 → 正式 Question 生成，draft 转为 published
    pub = client.post("/api/publish", json={"question_ids": [DRAFT_ID]})
    assert pub.status_code == 200
    assert pub.json()["published_count"] == 1

    question_before = _find_question(factory)
    assert question_before is not None
    assert question_before.review_status == "approved"

    # 3) 发布后再次保存 → 必须被封板拒绝
    r = client.patch(
        f"/api/questions/{DRAFT_ID}",
        json={"markdown": MARKDOWN + "\n追加的未授权修改"},
    )
    assert r.status_code == 409
    assert "已发布" in r.json()["detail"]

    # 4) OcrImportDraft 仍为 published，edited_markdown 未变化
    detail = client.get(f"/api/questions/{DRAFT_ID}").json()["question"]
    assert detail["review_status"] == "published"
    assert detail["edited_markdown"] == MARKDOWN

    # 5) 对应正式 Question 内容与 approved 状态不变
    question_after = _find_question(factory)
    assert question_after.id == question_before.id
    assert question_after.question_text == question_before.question_text
    assert question_after.review_status == "approved"


def test_revision_can_be_saved_and_submitted_for_review(client_and_factory):
    client, factory = client_and_factory
    client.patch(f"/api/questions/{DRAFT_ID}", json={"markdown": MARKDOWN})
    published = client.post("/api/publish", json={"question_ids": [DRAFT_ID]})
    assert published.json()["published_count"] == 1
    original = _find_question(factory)
    assert original is not None
    original_id = original.id
    original_text = original.question_text
    with factory.begin() as session:
        formal_count_before = session.scalar(select(func.count(Question.id)))
    assert formal_count_before == 1

    revision_response = client.post(f"/api/questions/{DRAFT_ID}/revision", json={})
    assert revision_response.status_code == 200
    revision = revision_response.json()["question"]
    revision_id = revision["question_id"]
    assert revision["review_status"] == "in_review"
    assert revision["formal_question_id"] == original_id
    assert revision["revision_of_id"] == DRAFT_ID

    revised_markdown = MARKDOWN.replace("求极限", "修订后求极限")
    saved = client.patch(
        f"/api/questions/{revision_id}", json={"markdown": revised_markdown}
    )
    assert saved.status_code == 200
    assert saved.json()["question"]["edited_markdown"] == revised_markdown
    assert _find_question(factory).question_text == original_text

    confirmed = client.post(f"/api/questions/{revision_id}/confirm-content", json={})
    assert confirmed.status_code == 200
    assert confirmed.json()["content_confirmed"] is True

    # 结构化元数据经由专用端点保存（不再从 Markdown 反解），提交以 DB 字段为事实来源
    meta = client.put(
        f"/api/questions/{revision_id}/metadata",
        json={"knowledge_points": ["函数的极限"], "difficulty_level": 3,
              "content_confirmed": True},
    )
    assert meta.status_code == 200

    submitted = client.post(
        f"/api/sources/{SOURCE_ID}/submit",
        json={"question_ids": [revision_id]},
    )
    assert submitted.status_code == 200
    assert submitted.json()["success_count"] == 1
    detail = client.get(f"/api/questions/{revision_id}").json()["question"]
    assert detail["review_status"] == "reviewed"
    assert detail["edited_markdown"] == revised_markdown

    published_revision = client.post("/api/publish", json={"question_ids": [revision_id]})
    assert published_revision.status_code == 200
    assert published_revision.json()["published_count"] == 1
    assert published_revision.json()["sync_details"][0]["bank_question_id"] == original_id
    with factory.begin() as session:
        formal_questions = list(session.scalars(select(Question)).all())
        assert len(formal_questions) == formal_count_before
        current = session.get(Question, original_id)
        assert current is not None
        assert current.question_text != original_text
        assert current.draft_id != original.draft_id
        revision_draft = session.get(OcrImportDraft, revision_id)
        assert revision_draft is not None
        assert revision_draft.formal_question_id == original_id
        assert revision_draft.revision_of_id == DRAFT_ID
        assert session.scalar(select(func.count(Question.id)).where(Question.id == original_id)) == 1

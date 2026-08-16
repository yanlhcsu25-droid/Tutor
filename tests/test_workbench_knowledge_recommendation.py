from fastapi.testclient import TestClient
import pytest
from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import CurriculumNode, KnowledgeNode, OcrImportDraft, OcrImportSource, Textbook
import calculus_agent.workbench.app as app_module
from calculus_agent.knowledge.classification import current_textbook_taxonomy


SOURCE_ID = "src_" + "c" * 32
QUESTION_ID = "q_" + "d" * 32


def _rule_fallback_result(session, **_kwargs):
    node = current_textbook_taxonomy(session)[0]
    point = {"knowledge_id": node.id, "name": node.name}
    return {
        "primary_knowledge_point_id": node.id,
        "secondary_knowledge_point_ids": [],
        "primary_knowledge_point": point,
        "secondary_knowledge_points": [],
        "candidate_knowledge_points": [point],
        "confidence": 0.0,
        "needs_review": True,
        "reason": "测试规则降级",
        "provenance": "rule_fallback",
        "fallback_reason": "llm_disabled",
        "llm_raw_response_type": None,
    }


@pytest.fixture(autouse=True)
def _isolate_real_llm(monkeypatch):
    """Workbench 单元测试不得读取 .env 后调用真实模型。"""
    monkeypatch.setattr(app_module, "classify_text_with_llm", _rule_fallback_result)


def _setup(tmp_path):
    factory = build_session_factory(f"sqlite:///{tmp_path / 'recommendation.db'}")
    create_schema(f"sqlite:///{tmp_path / 'recommendation.db'}")
    with factory.begin() as session:
        book = Textbook(name="高等数学", edition="测试", is_active=True)
        session.add(book)
        session.flush()
        chapter = CurriculumNode(
        textbook_id=book.id, node_type="chapter", title="导数与微分",
            sort_order=1, review_status="approved",
        )
        session.add(chapter)
        session.flush()
        section = CurriculumNode(
            textbook_id=book.id, parent_id=chapter.id, node_type="section",
            title="导数", sort_order=1, review_status="approved",
        )
        session.add(section)
        session.flush()
        session.add(KnowledgeNode(
            curriculum_node_id=section.id, node_type="concept", name="导数定义",
            normalized_name="导数定义", review_status="approved",
        ))
        session.add(OcrImportSource(
            id=SOURCE_ID, original_name="test.pdf", stored_path="/tmp/test.pdf",
            sha256="c" * 64, page_count=1, processing_status="done",
        ))
        session.add(OcrImportDraft(
            id=QUESTION_ID, source_id=SOURCE_ID, page_number=1, original_number="1",
            ocr_markdown="## 题目内容\n\n求导数\n",
            edited_markdown="## 题目内容\n\n求导数\n",
            review_status="in_review",
        ))
    return factory


def test_confirm_then_local_recommendation_is_independent(tmp_path, monkeypatch):
    factory = _setup(tmp_path)
    monkeypatch.setattr(app_module, "_session_factory", factory)
    client = TestClient(app_module.app)

    confirmed = client.post(f"/api/questions/{QUESTION_ID}/confirm-content")
    assert confirmed.status_code == 200
    assert confirmed.json()["content_confirmed"] is True

    recommendation = client.post(f"/api/questions/{QUESTION_ID}/knowledge/classify")
    assert recommendation.status_code == 200
    body = recommendation.json()
    assert body["provenance"] == "rule_fallback"
    assert 1 <= len(body["knowledge_points"]) <= 3
    assert body["knowledge_points"][0]["name"] == "导数定义"
    assert body["needs_review"] is True


def test_content_edit_clears_confirmation_but_not_metadata(tmp_path, monkeypatch):
    factory = _setup(tmp_path)
    monkeypatch.setattr(app_module, "_session_factory", factory)
    client = TestClient(app_module.app)
    client.post(f"/api/questions/{QUESTION_ID}/confirm-content")
    saved = client.patch(
        f"/api/questions/{QUESTION_ID}",
        json={"markdown": "## 题目内容\n\n求极限\n"},
    )
    assert saved.status_code == 200
    assert saved.json()["question"]["content_confirmed"] is False


def test_shadow_keeps_ai_snapshot_separate_from_human_truth(tmp_path, monkeypatch):
    factory = _setup(tmp_path)
    monkeypatch.setattr(app_module, "_session_factory", factory)
    client = TestClient(app_module.app)
    assert client.post(f"/api/questions/{QUESTION_ID}/confirm-content").status_code == 200

    recommendation = client.post(f"/api/questions/{QUESTION_ID}/knowledge/classify")
    assert recommendation.status_code == 200
    body = recommendation.json()
    ai_primary = body["primary_knowledge_point"]["knowledge_id"]

    # 第一次推荐被持久化；后续调用返回同一 AI 基线，而不是重新生成。
    repeated = client.post(f"/api/questions/{QUESTION_ID}/knowledge/classify")
    assert repeated.status_code == 200
    assert repeated.json()["primary_knowledge_point"]["knowledge_id"] == ai_primary

    reviewed = client.put(
        f"/api/questions/{QUESTION_ID}/knowledge/human-review",
        json={
            "primary_knowledge_point_id": ai_primary,
            "secondary_knowledge_point_ids": [],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    question = reviewed.json()["question"]
    assert question["knowledge_points"] == [ai_primary]
    assert question["knowledge_shadow"]["ai"]["primary_knowledge_point_id"] == ai_primary
    assert question["knowledge_shadow"]["human"]["modified"] is False

    stats = client.get("/api/knowledge/shadow/stats")
    assert stats.status_code == 200
    assert stats.json()["reviewed_total"] == 1
    assert stats.json()["primary_accuracy"] == 1


def test_content_edit_invalidates_ai_shadow_snapshot(tmp_path, monkeypatch):
    factory = _setup(tmp_path)
    monkeypatch.setattr(app_module, "_session_factory", factory)
    client = TestClient(app_module.app)
    client.post(f"/api/questions/{QUESTION_ID}/confirm-content")
    assert client.post(f"/api/questions/{QUESTION_ID}/knowledge/classify").status_code == 200
    saved = client.patch(
        f"/api/questions/{QUESTION_ID}",
        json={"markdown": "## 题目内容\n\n求极限\n"},
    )
    assert saved.status_code == 200
    assert saved.json()["question"]["knowledge_shadow"] is None


def test_unreviewed_rule_fallback_can_be_replaced_by_real_llm_result(
    tmp_path, monkeypatch
):
    factory = _setup(tmp_path)
    monkeypatch.setattr(app_module, "_session_factory", factory)
    client = TestClient(app_module.app)
    client.post(f"/api/questions/{QUESTION_ID}/confirm-content")

    fallback = client.post(f"/api/questions/{QUESTION_ID}/knowledge/classify")
    assert fallback.status_code == 200
    assert fallback.json()["provenance"] == "rule_fallback"
    point = fallback.json()["primary_knowledge_point"]

    def fake_llm_result(*_args, **_kwargs):
        return {
            "primary_knowledge_point_id": point["knowledge_id"],
            "secondary_knowledge_point_ids": [],
            "primary_knowledge_point": point,
            "secondary_knowledge_points": [],
            "candidate_knowledge_points": [point],
            "confidence": 0.91,
            "needs_review": False,
            "reason": "真实模型结构化结果",
            "provenance": "llm_suggested",
            "fallback_reason": None,
            "llm_raw_response_type": "parsed",
        }

    monkeypatch.setattr(app_module, "classify_text_with_llm", fake_llm_result)
    retried = client.post(f"/api/questions/{QUESTION_ID}/knowledge/classify")
    assert retried.status_code == 200
    assert retried.json()["provenance"] == "llm_suggested"
    assert retried.json()["knowledge_shadow"]["ai"]["confidence"] == 0.91

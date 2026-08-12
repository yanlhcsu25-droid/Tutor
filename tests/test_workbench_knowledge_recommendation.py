from fastapi.testclient import TestClient
from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import CurriculumNode, KnowledgeNode, OcrImportDraft, OcrImportSource, Textbook
import calculus_agent.workbench.app as app_module


SOURCE_ID = "src_" + "c" * 32
QUESTION_ID = "q_" + "d" * 32


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
    assert body["provenance"] == "rule_suggested"
    assert 1 <= len(body["knowledge_points"]) <= 3
    assert body["knowledge_points"][0]["name"] == "导数定义"


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

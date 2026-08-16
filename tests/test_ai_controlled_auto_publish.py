import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import calculus_agent.workbench.app as app_module
from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    OcrImportDraft,
    OcrImportSource,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    Textbook,
)
from calculus_agent.workbench.ai_content_review import (
    audit_content_with_llm,
    deterministic_content_issues,
    recommend_difficulty_with_llm,
)
from calculus_agent.workbench.markdown_schema import fixed_template


SOURCE_ID = "src_" + "7" * 32
QUESTION_ID = "q_" + "8" * 32


class FakeBackend:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {
            "model": "test-model",
            "message": {"content": json.dumps(self.payload, ensure_ascii=False)},
            "finish_reason": "stop",
        }


def _passing_audit(**_kwargs):
    return {
        "verdict": "PASS",
        "answer_relevant": True,
        "conclusion_consistent": True,
        "no_cross_question": True,
        "derivation_complete": True,
        "confidence": 0.61,
        "risk_codes": [],
        "reason": "答案与题目一致",
        "passed": True,
        "fallback_reason": None,
        "raw_response_type": "content",
        "model": "test-model",
    }


@pytest.fixture
def auto_publish_env(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'auto-publish.db'}"
    create_schema(db_url)
    factory = build_session_factory(db_url)
    with factory.begin() as session:
        textbook = Textbook(name="高等数学", is_active=True)
        session.add(textbook)
        session.flush()
        chapter = CurriculumNode(
            textbook_id=textbook.id,
            node_type="chapter",
            title="导数",
            sort_order=1,
            review_status="approved",
        )
        session.add(chapter)
        session.flush()
        section = CurriculumNode(
            textbook_id=textbook.id,
            parent_id=chapter.id,
            node_type="section",
            title="导数定义",
            sort_order=1,
            review_status="approved",
        )
        session.add(section)
        session.flush()
        node = KnowledgeNode(
            curriculum_node_id=section.id,
            node_type="concept",
            name="导数定义",
            normalized_name="导数定义",
            review_status="approved",
        )
        session.add(node)
        session.add(OcrImportSource(
            id=SOURCE_ID,
            original_name="auto.pdf",
            stored_path="/tmp/auto.pdf",
            sha256="7" * 64,
            page_count=1,
            processing_status="completed",
        ))
        markdown = fixed_template(
            "根据导数定义求函数在零点的导数。",
            question_type="calculation",
            page_number=1,
            original_number="1",
            answer="由差商极限可得导数等于 1。",
        )
        session.add(OcrImportDraft(
            id=QUESTION_ID,
            source_id=SOURCE_ID,
            page_number=1,
            original_number="1",
            ocr_markdown=markdown,
            edited_markdown=markdown,
            review_status="pending",
            match_status="matched",
        ))
        session.flush()
        knowledge_id = node.id

        example_draft = QuestionDraft(
            source_name="human_review",
            source_item_id="difficulty-example-1",
            subject="高等数学",
            question_type="calculation",
            question_text="根据导数定义求函数在一点的导数。",
            reference_answers_json=["1"],
            answer_types_json=["number"],
            solution_text="写出差商并取极限，结果为 1。",
            normalized_fingerprint="9" * 64,
            status="approved",
        )
        session.add(example_draft)
        session.flush()
        example_question = Question(
            draft_id=example_draft.id,
            question_text=example_draft.question_text,
            question_type="calculation",
            final_answer="1",
            solution_json={"text": example_draft.solution_text},
            verification_status="verified",
            review_status="approved",
            is_active=True,
        )
        session.add(example_question)
        session.flush()
        session.add(QuestionKnowledgeLink(
            question_id=example_question.id,
            knowledge_node_id=knowledge_id,
            relation_type="primary",
            confidence=1.0,
            evidence_json=[{"source": "human"}],
        ))
        session.add(QuestionProfile(
            question_id=example_question.id,
            profile_version=1,
            difficulty=2,
            estimated_time_min=5,
            reasoning_depth=2,
            calculation_load=2,
            knowledge_depth=2,
            comprehensive_level=1,
            confidence=1.0,
            profile_source="human",
            profile_status="approved",
            reason="教师确认的常规单方法题",
        ))

    def valid_knowledge(*_args, **_kwargs):
        point = {"knowledge_id": knowledge_id, "name": "导数定义"}
        return {
            "primary_knowledge_point_id": knowledge_id,
            "secondary_knowledge_point_ids": [],
            "primary_knowledge_point": point,
            "secondary_knowledge_points": [],
            "candidate_knowledge_points": [point],
            "confidence": 0.73,
            "needs_review": False,
            "reason": "核心考查导数定义",
            "provenance": "llm_suggested",
            "fallback_reason": None,
            "llm_raw_response_type": "content",
        }

    monkeypatch.setattr(app_module, "_session_factory", factory)
    monkeypatch.setattr(app_module, "audit_content_with_llm", _passing_audit)
    monkeypatch.setattr(app_module, "classify_text_with_llm", valid_knowledge)

    def valid_difficulty(*_args, **_kwargs):
        return {
            "difficulty_level": 2,
            "confidence": 0.82,
            "needs_review": False,
            "reason": "与教师确认的同知识点常规题难度相近",
            "provenance": "llm_suggested",
            "fallback_reason": None,
            "raw_response_type": "content",
            "model": "test-model",
            "example_count": 1,
        }

    monkeypatch.setattr(
        app_module,
        "recommend_difficulty_with_llm",
        valid_difficulty,
    )
    return TestClient(app_module.app), factory, knowledge_id


def test_difficulty_recommendation_uses_human_labeled_examples(auto_publish_env):
    _client, factory, knowledge_id = auto_publish_env
    backend = FakeBackend({
        "difficulty_level": 2,
        "confidence": 0.8,
        "needs_review": False,
        "reason": "与同题型、同知识点的人工作答样例相近",
    })

    with factory.begin() as session:
        result = recommend_difficulty_with_llm(
            session,
            question_body="根据导数定义求函数在零点的导数。",
            standard_solution="由差商极限可得导数等于 1。",
            question_type="calculation",
            knowledge_ids=[knowledge_id],
            backend=backend,
        )

    assert result["provenance"] == "llm_suggested"
    assert result["difficulty_level"] == 2
    assert result["example_count"] == 1
    messages = backend.calls[0][0][0]
    prompt_payload = json.loads(messages[1]["content"])
    assert prompt_payload["human_labeled_examples"][0]["difficulty_level"] == 2


def test_difficulty_recommendation_without_human_examples_still_calls_llm(session):
    backend = FakeBackend({
        "difficulty_level": 3,
        "confidence": 0.72,
        "needs_review": False,
        "reason": "需要多步积分变换，按统一标准评为三级",
    })

    result = recommend_difficulty_with_llm(
        session,
        question_body="计算一个需要两次变换的积分。",
        standard_solution="先换元，再分部积分，最后代回。",
        question_type="计算题",
        knowledge_ids=[],
        backend=backend,
    )

    assert result["provenance"] == "llm_suggested"
    assert result["difficulty_level"] == 3
    assert result["example_count"] == 0
    assert result["needs_review"] is True
    prompt_payload = json.loads(backend.calls[0][0][0][1]["content"])
    assert prompt_payload["human_labeled_examples"] == []


def test_manual_classification_returns_and_prefills_ai_difficulty(auto_publish_env):
    client, factory, _knowledge_id = auto_publish_env
    confirmed = client.post(f"/api/questions/{QUESTION_ID}/confirm-content")
    assert confirmed.status_code == 200

    response = client.post(f"/api/questions/{QUESTION_ID}/knowledge/classify")

    assert response.status_code == 200, response.text
    assert response.json()["difficulty_result"]["provenance"] == "llm_suggested"
    assert response.json()["difficulty_result"]["difficulty_level"] == 2
    with factory.begin() as session:
        draft = session.get(OcrImportDraft, QUESTION_ID)
        assert draft.difficulty_level == 2
        assert draft.knowledge_shadow_json["ai"]["difficulty_result"][
            "difficulty_level"
        ] == 2


def test_structured_content_audit_pass_does_not_require_confidence_threshold():
    result = audit_content_with_llm(
        question_body="求导数",
        standard_solution="答案为 1",
        question_type="计算题",
        backend=FakeBackend({
            "verdict": "PASS",
            "answer_relevant": True,
            "conclusion_consistent": True,
            "no_cross_question": True,
            "derivation_complete": True,
            "confidence": 0.55,
            "risk_codes": [],
            "reason": "一致",
        }),
    )
    assert result["passed"] is True
    assert result["confidence"] == 0.55


def test_structured_content_audit_rejects_any_explicit_risk():
    result = audit_content_with_llm(
        question_body="求导数",
        standard_solution="答案为 1",
        question_type="计算题",
        backend=FakeBackend({
            "verdict": "PASS",
            "answer_relevant": True,
            "conclusion_consistent": True,
            "no_cross_question": True,
            "derivation_complete": True,
            "confidence": 0.99,
            "risk_codes": ["possible_cross_question"],
            "reason": "疑似串题",
        }),
    )
    assert result["passed"] is False


def test_deterministic_gate_rejects_ocr_placeholder():
    markdown = fixed_template(
        "求函数极限。",
        question_type="calculation",
        page_number=1,
        original_number="1",
        answer="[图片内容暂未解析，请人工核对原PDF]",
    )
    issues, _payload = deterministic_content_issues({
        "question_id": QUESTION_ID,
        "source_file_id": SOURCE_ID,
        "edited_markdown": markdown,
        "ocr_markdown": markdown,
        "match_status": "matched",
    })
    assert "ocr_placeholder_remaining" in issues


def test_controlled_auto_publish_persists_provenance_and_profile(auto_publish_env):
    client, factory, knowledge_id = auto_publish_env
    response = client.post(
        f"/api/sources/{SOURCE_ID}/ai-auto-publish",
        json={},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["published_count"] == 1
    assert body["manual_review_count"] == 0

    with factory.begin() as session:
        draft = session.get(OcrImportDraft, QUESTION_ID)
        assert draft.review_status == "published"
        assert draft.publish_source == "ai_auto"
        assert draft.ai_review_json["passed"] is True
        assert draft.ai_review_json["difficulty_result"]["provenance"] == "llm_suggested"
        assert draft.ai_review_json["difficulty_level"] == 2
        question = session.get(Question, draft.formal_question_id)
        assert question.publish_source == "ai_auto"
        assert question.ai_review_json["model"] == "test-model"
        assert question.published_at is not None
        link = session.scalar(select(QuestionKnowledgeLink).where(
            QuestionKnowledgeLink.question_id == question.id
        ))
        assert link.knowledge_node_id == knowledge_id
        assert link.evidence_json[0]["source"] == "ai_auto"
        profile = session.scalar(select(QuestionProfile).where(
            QuestionProfile.question_id == question.id
        ))
        assert profile.profile_source == "auto"
        assert profile.profile_status == "approved"
        assert profile.difficulty == 2


def test_difficulty_llm_failure_stays_in_manual_queue(
    auto_publish_env,
    monkeypatch,
):
    client, factory, _knowledge_id = auto_publish_env

    def failed_difficulty(*_args, **_kwargs):
        return {
            "difficulty_level": 2,
            "confidence": 0.0,
            "needs_review": True,
            "reason": "AI 难度推荐未完成，规则估算仅供人工参考",
            "provenance": "rule_fallback",
            "fallback_reason": "llm_timeout",
            "raw_response_type": None,
            "model": None,
            "example_count": 1,
        }

    monkeypatch.setattr(
        app_module,
        "recommend_difficulty_with_llm",
        failed_difficulty,
    )
    response = client.post(
        f"/api/sources/{SOURCE_ID}/ai-auto-publish",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["published_count"] == 0
    assert response.json()["manual_review_count"] == 1
    with factory.begin() as session:
        draft = session.get(OcrImportDraft, QUESTION_ID)
        assert draft.review_status == "in_review"
        assert draft.ai_review_json["risk_codes"] == [
            "difficulty_classification_failed"
        ]
        assert draft.ai_review_json["difficulty_result"]["fallback_reason"] == (
            "llm_timeout"
        )


def test_ai_published_profile_can_be_human_reviewed_without_editing_content(
    auto_publish_env,
):
    client, factory, original_knowledge_id = auto_publish_env
    published = client.post(
        f"/api/sources/{SOURCE_ID}/ai-auto-publish",
        json={},
    )
    assert published.json()["published_count"] == 1

    with factory.begin() as session:
        original_node = session.get(KnowledgeNode, original_knowledge_id)
        replacement = KnowledgeNode(
            curriculum_node_id=original_node.curriculum_node_id,
            node_type="concept",
            name="复合函数求导",
            normalized_name="复合函数求导",
            review_status="approved",
        )
        session.add(replacement)
        session.flush()
        replacement_id = replacement.id
        draft = session.get(OcrImportDraft, QUESTION_ID)
        formal = session.get(Question, draft.formal_question_id)
        original_text = formal.question_text

    reviewed = client.put(
        f"/api/questions/{QUESTION_ID}/ai-published-profile-review",
        json={
            "primary_knowledge_point_id": replacement_id,
            "secondary_knowledge_point_ids": [original_knowledge_id],
            "difficulty_level": 5,
            "modification_reason": "人工抽检修正",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["question"]["knowledge_points"] == [
        replacement_id,
        original_knowledge_id,
    ]
    assert reviewed.json()["question"]["difficulty_level"] == 5

    with factory.begin() as session:
        draft = session.get(OcrImportDraft, QUESTION_ID)
        formal = session.get(Question, draft.formal_question_id)
        assert formal.question_text == original_text
        assert draft.knowledge_shadow_json["ai"][
            "primary_knowledge_point_id"
        ] == original_knowledge_id
        assert draft.knowledge_shadow_json["human"][
            "primary_knowledge_point_id"
        ] == replacement_id
        assert draft.knowledge_shadow_json["human"]["difficulty_level"] == 5
        links = list(session.scalars(
            select(QuestionKnowledgeLink).where(
                QuestionKnowledgeLink.question_id == formal.id
            )
        ))
        assert {item.knowledge_node_id for item in links} == {
            replacement_id,
            original_knowledge_id,
        }
        assert all(
            item.evidence_json[0]["source"] == "ai_auto_human_review"
            for item in links
        )
        profiles = list(session.scalars(
            select(QuestionProfile)
            .where(QuestionProfile.question_id == formal.id)
            .order_by(QuestionProfile.profile_version)
        ))
        assert [item.profile_source for item in profiles] == ["auto", "human"]
        assert profiles[-1].difficulty == 5


def test_hard_check_failure_stays_in_manual_queue(auto_publish_env):
    client, factory, _knowledge_id = auto_publish_env
    with factory.begin() as session:
        draft = session.get(OcrImportDraft, QUESTION_ID)
        draft.edited_markdown = draft.edited_markdown.replace(
            "由差商极限可得导数等于 1。",
            "[图片内容暂未解析，请人工核对原PDF]",
        )

    response = client.post(
        f"/api/sources/{SOURCE_ID}/ai-auto-publish",
        json={},
    )
    assert response.status_code == 200
    assert response.json()["published_count"] == 0
    assert response.json()["manual_review_count"] == 1
    with factory.begin() as session:
        draft = session.get(OcrImportDraft, QUESTION_ID)
        assert draft.review_status == "in_review"
        assert draft.ai_review_json["passed"] is False
        assert "ocr_placeholder_remaining" in draft.ai_review_json["risk_codes"]
    detail = client.get(f"/api/questions/{QUESTION_ID}").json()["question"]
    assert detail["validation"] is None

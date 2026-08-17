from sqlalchemy import select

from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.knowledge.classification import (
    classify_knowledge_points,
    confirm_question_knowledge,
    current_textbook_taxonomy,
    ensure_calculus_taxonomy,
    generate_knowledge_candidates,
    suggest_question_knowledge,
)
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    Textbook,
)


class FakeBackend:
    def __init__(self, payload=None, error=None, response=None):
        self.payload = payload
        self.error = error
        self.response = response
        self.tool_choice = None
        self.response_format = None

    def complete(self, messages, tools, *, tool_choice="auto", response_format=None):
        self.tool_choice = tool_choice
        self.response_format = response_format
        if self.error:
            raise self.error
        if self.response is not None:
            return self.response
        return {"message": {"content": __import__("json").dumps(self.payload)}}


def _published_question(session) -> Question:
    draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id="ocr-1",
        variant=1,
        subject="高等数学",
        grade="大学",
        question_type="calculation",
        question_text=r"求极限 $\lim_{x\to0}\frac{\sin x}{x}$",
        reference_answers_json=["1"],
        normalized_fingerprint="k" * 64,
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        grade=draft.grade,
        question_type=draft.question_type,
        final_answer="1",
        solution_json={"solution_steps": ["使用重要极限"]},
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    return question


def test_suggests_only_controlled_calculus_nodes(session):
    question = _published_question(session)
    suggestions = suggest_question_knowledge(session, question)
    assert suggestions
    assert suggestions[0]["name"] in {"函数极限", "两个重要极限"}
    assert all(item["knowledge_node_id"] for item in suggestions)


def test_confirm_replaces_question_knowledge_links(session):
    question = _published_question(session)
    nodes = ensure_calculus_taxonomy(session)
    selected = [nodes[0].id, nodes[4].id]
    confirm_question_knowledge(session, question.id, selected)
    links = list(session.scalars(select(QuestionKnowledgeLink).where(
        QuestionKnowledgeLink.question_id == question.id
    )).all())
    assert [link.knowledge_node_id for link in links] == selected
    assert [link.relation_type for link in links] == ["related", "related"]


def test_llm_rejects_id_outside_candidates(session):
    question = _published_question(session)
    result = classify_knowledge_points(session, question, backend=FakeBackend({
        "primary_knowledge_point_id": "outside-taxonomy",
        "secondary_knowledge_point_ids": [],
        "confidence": 0.95,
        "needs_review": False,
        "reason": "invalid",
    }))
    assert result["provenance"] == "rule_fallback"
    assert result["needs_review"] is True


def test_valid_llm_result_preserves_primary_and_secondary_roles(session):
    question = _published_question(session)
    by_name = {item["name"]: item["knowledge_node_id"] for item in suggest_question_knowledge(session, question)}
    result = classify_knowledge_points(session, question, backend=FakeBackend({
        "primary_knowledge_point_id": by_name["两个重要极限"],
        "secondary_knowledge_point_ids": [by_name["函数极限"]],
        "confidence": 0.91,
        "needs_review": False,
        "reason": "解题直接使用重要极限，函数极限是上位概念",
    }))
    assert result["provenance"] == "llm_suggested"
    assert result["primary_knowledge_point"]["name"] == "两个重要极限"
    assert result["secondary_knowledge_points"][0]["name"] == "函数极限"
    assert [item["role"] for item in result["knowledge_points"]] == ["primary", "secondary"]


def test_low_llm_confidence_always_needs_review(session):
    question = _published_question(session)
    candidate_id = suggest_question_knowledge(session, question)[0]["knowledge_node_id"]
    result = classify_knowledge_points(session, question, backend=FakeBackend({
        "primary_knowledge_point_id": candidate_id,
        "secondary_knowledge_point_ids": [],
        "confidence": 0.4,
        "needs_review": False,
        "reason": "把握不足",
    }))
    assert result["provenance"] == "llm_suggested"
    assert result["needs_review"] is True


def test_llm_rejects_primary_secondary_duplicate(session):
    question = _published_question(session)
    candidate_id = suggest_question_knowledge(session, question)[0]["knowledge_node_id"]
    result = classify_knowledge_points(session, question, backend=FakeBackend({
        "primary_knowledge_point_id": candidate_id,
        "secondary_knowledge_point_ids": [candidate_id],
        "confidence": 0.9,
        "needs_review": False,
        "reason": "duplicate",
    }))
    assert result["provenance"] == "rule_fallback"
    assert result["needs_review"] is True


def test_llm_rejects_more_than_two_secondary_points(session):
    question = _published_question(session)
    ids = [item.id for item in ensure_calculus_taxonomy(session)[:4]]
    result = classify_knowledge_points(session, question, backend=FakeBackend({
        "primary_knowledge_point_id": ids[0],
        "secondary_knowledge_point_ids": ids[1:4],
        "confidence": 0.9,
        "needs_review": False,
        "reason": "too many",
    }))
    assert result["provenance"] == "rule_fallback"
    assert result["needs_review"] is True


def test_llm_rejects_confidence_above_one(session):
    question = _published_question(session)
    candidate_id = suggest_question_knowledge(session, question)[0]["knowledge_node_id"]
    result = classify_knowledge_points(session, question, backend=FakeBackend({
        "primary_knowledge_point_id": candidate_id,
        "secondary_knowledge_point_ids": [],
        "confidence": 1.1,
        "needs_review": False,
        "reason": "invalid confidence",
    }))
    assert result["provenance"] == "rule_fallback"
    assert result["confidence"] <= 0.59
    assert result["fallback_reason"] == "schema_validation_error"
    assert {"field": "confidence", "category": "confidence_out_of_range"} in result["schema_validation_errors"]


def _textbook_node(session, *, textbook, chapter_title, point_name, order):
    chapter = CurriculumNode(
        textbook_id=textbook.id,
        node_type="chapter",
        title=chapter_title,
        sort_order=order,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()
    section = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=chapter.id,
        node_type="section",
        title=point_name,
        sort_order=order,
        review_status="approved",
    )
    session.add(section)
    session.flush()
    node = KnowledgeNode(
        curriculum_node_id=section.id,
        node_type="knowledge_point",
        name=point_name,
        normalized_name=point_name,
        review_status="approved",
    )
    session.add(node)
    session.flush()
    return node


def test_cross_textbook_id_is_rejected(session):
    active = Textbook(name="当前高数教材", is_active=True)
    other = Textbook(name="其他教材", is_active=False)
    session.add_all([active, other])
    session.flush()
    current = _textbook_node(
        session, textbook=active, chapter_title="第四章", point_name="分部积分法", order=4
    )
    foreign = _textbook_node(
        session, textbook=other, chapter_title="积分", point_name="积分技巧（其他教材）", order=1
    )
    question = _published_question(session)
    question.question_text = "求不定积分"
    question.solution_json = {"solution_steps": ["使用分部积分法"]}
    result = classify_knowledge_points(session, question, backend=FakeBackend({
        "primary_knowledge_point_id": foreign.id,
        "secondary_knowledge_point_ids": [],
        "confidence": 0.9,
        "needs_review": False,
        "reason": "foreign",
    }))
    assert current.id in {item.id for item in current_textbook_taxonomy(session)}
    assert result["provenance"] == "rule_fallback"
    assert result["needs_review"] is True


def test_llm_unavailable_falls_back_without_blocking(session):
    question = _published_question(session)
    result = classify_knowledge_points(
        session, question, backend=FakeBackend(error=TimeoutError("timeout"))
    )
    assert result["knowledge_points"]
    assert result["provenance"] == "rule_fallback"
    assert result["needs_review"] is True


def test_classification_uses_all_chapters_of_current_textbook(session):
    book = Textbook(name="高等数学全册", is_active=True)
    session.add(book)
    session.flush()
    _textbook_node(session, textbook=book, chapter_title="第一章", point_name="函数极限", order=1)
    target = _textbook_node(session, textbook=book, chapter_title="第四章", point_name="分部积分法", order=4)
    candidates = generate_knowledge_candidates(
        session,
        question_body=r"求 $\int x e^x dx$",
        standard_solution="",
        solution_steps=["使用分部积分法"],
    )
    assert target.id in {item.id for item in candidates}


def test_candidate_recall_recognizes_lhospital_from_solution_method(session):
    candidates = generate_knowledge_candidates(
        session,
        question_body=r"求 $\lim_{x\to0}(e^x-1-x)/x^2$",
        standard_solution="分子分母求导两次",
        solution_steps=["分子分母求导两次后得到极限"],
    )
    assert "洛必达法则" in {item.name for item in candidates}


def test_candidate_recall_recognizes_derivative_definition_from_difference_quotient(session):
    candidates = generate_knowledge_candidates(
        session,
        question_body=r"已知 $f(x)=x^2$，用定义求 $f'(1)$",
        standard_solution="由差商极限计算",
        solution_steps=["写出差商并令增量趋于零"],
    )
    assert "导数定义" in {item.name for item in candidates}


def _three_level_directory_kn(session, *, textbook, parent_title, point_name, order):
    """Mirror a textbook-imported three-level knowledge point.

    These KNs have no aliases/keywords and are absent from CALCULUS_TAXONOMY,
    so they are only recallable if the retriever matches on the compound name
    itself (not just a verbatim whole-string substring).
    """
    chapter = CurriculumNode(
        textbook_id=textbook.id,
        node_type="chapter",
        title="第二章 导数与微分" if "参数方程" in point_name or "导数" in point_name else "第三章 微分中值定理与导数的应用",
        sort_order=order,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()
    parent = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=chapter.id,
        node_type="section",
        title=parent_title,
        sort_order=order,
        review_status="approved",
    )
    session.add(parent)
    session.flush()
    node = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=parent.id,
        node_type="topic",
        title=point_name,
        sort_order=order,
        review_status="approved",
    )
    session.add(node)
    session.flush()
    kn = KnowledgeNode(
        curriculum_node_id=node.id,
        node_type="concept",
        name=point_name,
        normalized_name=normalize_name(point_name),
        source_type="directory",
        confidence=1.0,
        review_status="approved",
    )
    session.add(kn)
    session.flush()
    return kn


def test_three_level_kp_recalled_without_alias_or_keyword(session):
    """Regression: a fine-grained three-level directory KP with no alias and no
    keyword must still be recalled when the question mentions a sub-string of
    its compound name.

    Input question: "参数方程确定函数，要求判断凹凸性"
    Expected candidates MUST include:
        2.4.4 参数方程确定函数的二阶导数
        3.4.2 曲线凹凸性的判定
    """
    book = Textbook(name="高等数学（回归）", is_active=True)
    session.add(book)
    session.flush()
    _three_level_directory_kn(
        session, textbook=book, parent_title="隐函数与参数方程求导",
        point_name="参数方程确定函数的二阶导数", order=10,
    )
    _three_level_directory_kn(
        session, textbook=book, parent_title="隐函数与参数方程求导",
        point_name="参数方程确定函数的导数", order=11,
    )
    _three_level_directory_kn(
        session, textbook=book, parent_title="函数的单调性与曲线的凹凸性",
        point_name="曲线凹凸性的判定", order=30,
    )

    candidates = generate_knowledge_candidates(
        session,
        question_body="参数方程确定函数，要求判断凹凸性",
        standard_solution="",
        solution_steps=[],
        limit=20,
    )
    names = {c.name for c in candidates}
    assert "参数方程确定函数的二阶导数" in names, (
        f"expected three-level KP recalled, got: {sorted(names)}"
    )
    assert "曲线凹凸性的判定" in names, (
        f"expected three-level KP recalled, got: {sorted(names)}"
    )

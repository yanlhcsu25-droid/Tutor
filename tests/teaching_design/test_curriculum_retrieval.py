from calculus_agent.agent.tool_registry import AgentExecutionContext, build_agent_tools
from calculus_agent.application.curriculum_retrieval import (
    retrieve_curriculum_candidates,
)
from calculus_agent.knowledge.rag.embedding import LocalHashingEmbedding
from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook


def _taxonomy(session):
    textbook = Textbook(name="高等数学上册", is_active=True)
    session.add(textbook)
    session.flush()

    limit_chapter = CurriculumNode(
        textbook_id=textbook.id,
        node_type="chapter",
        code="1",
        title="第一章 函数与极限",
        sort_order=1,
        review_status="approved",
    )
    derivative_chapter = CurriculumNode(
        textbook_id=textbook.id,
        node_type="chapter",
        code="3",
        title="第三章 导数与微分",
        sort_order=3,
        review_status="approved",
    )
    session.add_all([limit_chapter, derivative_chapter])
    session.flush()

    limit_section = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=limit_chapter.id,
        node_type="section",
        code="1.6",
        title="函数的极限",
        sort_order=6,
        review_status="approved",
    )
    lhopital_section = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=derivative_chapter.id,
        node_type="section",
        code="3.8",
        title="洛必达法则",
        sort_order=8,
        review_status="approved",
    )
    session.add_all([limit_section, lhopital_section])
    session.flush()
    session.add_all([
        KnowledgeNode(
            curriculum_node_id=limit_section.id,
            node_type="concept",
            name="无穷小与极限运算",
            normalized_name="无穷小与极限运算",
            review_status="approved",
        ),
        KnowledgeNode(
            curriculum_node_id=lhopital_section.id,
            node_type="knowledge_point",
            name="洛必达法则",
            normalized_name="洛必达法则",
            description="使用导数计算未定式极限",
            review_status="approved",
        ),
    ])
    session.flush()
    return limit_chapter, derivative_chapter


def test_natural_language_limit_query_recalls_owning_chapter(session):
    limit_chapter, _ = _taxonomy(session)

    candidates = retrieve_curriculum_candidates(
        session,
        query="学生极限不好",
        top_k=5,
        embedding_provider=LocalHashingEmbedding(dim=256),
    )

    assert any(item.node_id == limit_chapter.id for item in candidates)
    assert all(0 <= item.similarity_score <= 1 for item in candidates)


def test_lhopital_query_recalls_derivative_path(session):
    _, derivative_chapter = _taxonomy(session)

    candidates = retrieve_curriculum_candidates(
        session,
        query="学生不会洛必达",
        top_k=5,
        embedding_provider=LocalHashingEmbedding(dim=256),
    )

    assert any(item.node_id == derivative_chapter.id for item in candidates)
    assert any(
        item.title == "洛必达法则"
        and "第三章 导数与微分" in item.parent_path
        for item in candidates
    )


def test_agent_tool_returns_candidates_without_selecting_scope(session, monkeypatch):
    _taxonomy(session)
    monkeypatch.setattr(
        "calculus_agent.application.curriculum_retrieval.get_embedding_provider",
        lambda: LocalHashingEmbedding(dim=256),
    )
    context = AgentExecutionContext(
        session=session,
        conversation_id="curriculum-retrieval",
        paper_id=None,
        version_id=None,
        state_store=None,
    )
    tool = build_agent_tools(context)["retrieve_curriculum_candidates"]

    execution = tool.execute(tool.input_model.model_validate({
        "query": "学生极限不好",
        "top_k": 3,
    }))

    assert execution.status == "completed"
    assert execution.payload["ok"] is True
    assert execution.payload["scope_selected"] is False
    assert execution.payload["semantic_matches"]
    assert execution.payload["selectable_scopes"]
    assert all(
        "parent_path" in item
        for item in execution.payload["selectable_scopes"]
    )
    semantic_concept = next(
        item
        for item in execution.payload["semantic_matches"]
        if item["title"] == "无穷小与极限运算"
    )
    assert semantic_concept["node_type"] == "concept"
    assert semantic_concept["node_id"] not in {
        item["node_id"]
        for item in execution.payload["selectable_scopes"]
    }
    assert {"第一章 函数与极限", "函数的极限"}.issubset({
        item["title"]
        for item in execution.payload["selectable_scopes"]
    })

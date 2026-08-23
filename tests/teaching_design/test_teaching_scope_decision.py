import json

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.agent.tool_registry import AgentExecutionContext, build_agent_tools
from calculus_agent.application.curriculum_retrieval import CurriculumCandidate
from calculus_agent.knowledge.rag.embedding import LocalHashingEmbedding
from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook


def _taxonomy(session):
    textbook = Textbook(name="高等数学上册", is_active=True)
    session.add(textbook)
    session.flush()
    chapter = CurriculumNode(
        textbook_id=textbook.id,
        node_type="chapter",
        code="1",
        title="第一章 函数与极限",
        sort_order=1,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()
    section = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=chapter.id,
        node_type="section",
        code="1.6",
        title="函数的极限",
        sort_order=6,
        review_status="approved",
    )
    session.add(section)
    session.flush()
    knowledge = KnowledgeNode(
        curriculum_node_id=section.id,
        node_type="knowledge_point",
        name="无穷小与极限运算",
        normalized_name="无穷小与极限运算",
        review_status="approved",
    )
    session.add(knowledge)
    session.flush()
    return chapter, knowledge


def _context(session, candidates):
    context = AgentExecutionContext(
        session=session,
        conversation_id="scope-decision",
        paper_id=None,
        version_id=None,
        state_store=DatabasePendingReplacementStore(session),
    )
    context.inspection_state["selectable_teaching_scopes"] = [
        item.model_dump(mode="json") for item in candidates
    ]
    return context


def test_chapter_scope_selection_is_validated_and_saved(session):
    chapter, _ = _taxonomy(session)
    context = _context(session, [CurriculumCandidate(
        node_id=chapter.id,
        node_type="chapter",
        title=chapter.title,
        parent_path=[],
        similarity_score=1,
    )])
    tool = build_agent_tools(context)["select_teaching_scope"]
    assert "scope_level" not in tool.input_model.model_json_schema()["properties"]

    execution = tool.execute(tool.input_model.model_validate({
        "selected_node_ids": [chapter.id],
        "reasoning": "教师的问题与函数极限章节直接相关。",
    }))

    assert execution.status == "completed"
    assert execution.payload["validated_scope_names"] == ["第一章 函数与极限"]
    assert execution.payload["inferred_scope_level"] == "chapter"
    memory = context.state_store.get_memory("scope-decision")
    assert memory.active_task["status"] == "scope_selected"
    assert memory.active_task["waiting_for_scope"] is False


def test_knowledge_scope_selection_resolves_owning_chapter(session):
    _, knowledge = _taxonomy(session)
    context = _context(session, [CurriculumCandidate(
        node_id=knowledge.id,
        node_type="knowledge_point",
        title=knowledge.name,
        parent_path=["第一章 函数与极限", "函数的极限"],
        similarity_score=1,
    )])
    tool = build_agent_tools(context)["select_teaching_scope"]

    execution = tool.execute(tool.input_model.model_validate({
        "selected_node_ids": [knowledge.id],
        # Deprecated model output is ignored; DB facts infer knowledge level.
        "scope_level": "section",
        "reasoning": "问题集中在无穷小和极限运算。",
    }))

    assert execution.status == "completed"
    assert execution.payload["selected_knowledge_names"] == ["无穷小与极限运算"]
    assert execution.payload["inferred_scope_level"] == "knowledge"
    assert execution.payload["validated_scope_names"] == ["第一章 函数与极限"]


def test_node_outside_retrieval_candidates_is_rejected(session):
    chapter, _ = _taxonomy(session)
    context = _context(session, [CurriculumCandidate(
        node_id=chapter.id,
        node_type="chapter",
        title=chapter.title,
        parent_path=[],
        similarity_score=1,
    )])
    tool = build_agent_tools(context)["select_teaching_scope"]

    execution = tool.execute(tool.input_model.model_validate({
        "selected_node_ids": ["invented-node-id"],
        "reasoning": "模型自行生成了节点。",
    }))

    assert execution.status == "completed"
    assert execution.payload["code"] == "teaching_scope_not_in_candidates"
    assert "message" not in execution.payload
    assert not execution.result_fields.get("blocking_errors")
    assert all(term not in execution.payload["retry_instruction"] for term in (
        "CurriculumNode",
        "KnowledgeNode",
        "scope_level",
        "selected_node_ids",
    ))


def test_low_score_semantic_hit_is_not_selectable(session, monkeypatch):
    monkeypatch.setattr(
        "calculus_agent.agent.tool_adapters.teaching_environment.retrieve_curriculum_candidates",
        lambda *_args, **_kwargs: [CurriculumCandidate(
            node_id="low-score-node",
            node_type="chapter",
            title="最近但不相关",
            parent_path=[],
            similarity_score=0.04,
        )],
    )
    context = AgentExecutionContext(
        session=session,
        conversation_id="low-score",
        paper_id=None,
        version_id=None,
        state_store=DatabasePendingReplacementStore(session),
    )
    tool = build_agent_tools(context)["retrieve_curriculum_candidates"]
    execution = tool.execute(tool.input_model.model_validate({"query": "矩阵特征值"}))

    assert execution.status == "needs_clarification"
    assert execution.payload["semantic_matches"][0]["title"] == "最近但不相关"
    assert execution.payload["selectable_scopes"] == []
    assert execution.payload["candidate_min_score"] == 0.10


def test_retrieval_without_legal_scope_enters_clarification(session, monkeypatch):
    monkeypatch.setattr(
        "calculus_agent.agent.tool_adapters.teaching_environment.retrieve_curriculum_candidates",
        lambda *_args, **_kwargs: [CurriculumCandidate(
            node_id="semantic-only-concept",
            node_type="concept",
            title="极限思想",
            parent_path=[],
            similarity_score=0.9,
        )],
    )
    context = AgentExecutionContext(
        session=session,
        conversation_id="empty-candidates",
        paper_id=None,
        version_id=None,
        state_store=DatabasePendingReplacementStore(session),
    )
    tool = build_agent_tools(context)["retrieve_curriculum_candidates"]

    execution = tool.execute(tool.input_model.model_validate({
        "query": "不存在的教学主题",
    }))

    assert execution.status == "needs_clarification"
    assert execution.payload["code"] == "curriculum_candidates_not_found"
    assert execution.payload["semantic_matches"][0]["title"] == "极限思想"
    assert execution.payload["selectable_scopes"] == []


class ScopeFlowBackend:
    def __init__(self):
        self.calls = 0

    @staticmethod
    def tool(name, arguments):
        return {"message": {"tool_calls": [{
            "id": name,
            "function": {"name": name, "arguments": arguments},
        }]}}

    def complete(self, messages, tools):
        self.calls += 1
        observations = {
            item.get("name"): json.loads(item["content"])
            for item in messages
            if item.get("role") == "tool"
        }
        if "retrieve_curriculum_candidates" not in observations:
            return self.tool("retrieve_curriculum_candidates", {
                "query": "学生极限不好",
                "top_k": 5,
            })
        if "select_teaching_scope" not in observations:
            chapter = next(
                item for item in observations["retrieve_curriculum_candidates"]["selectable_scopes"]
                if item["node_type"] == "chapter"
            )
            return self.tool("select_teaching_scope", {
                "selected_node_ids": [chapter["node_id"]],
                "reasoning": "根据可选教学范围选择对应章节。",
            })
        scope = observations["select_teaching_scope"]["validated_scope_names"]
        if "inspect_curriculum" not in observations:
            visible = {item["function"]["name"] for item in tools}
            assert {"inspect_curriculum", "inspect_question_bank", "create_teaching_design"}.issubset(visible)
            return self.tool("inspect_curriculum", {"scope_names": scope})
        if "inspect_question_bank" not in observations:
            return self.tool("inspect_question_bank", {
                "scope_names": scope,
                "detail_level": "aggregate",
            })
        if "create_teaching_design" not in observations:
            return self.tool("create_teaching_design", {
                "content": json.dumps({
                    "title": "函数与极限基础复习",
                    "objective": "理解无穷小并掌握极限运算。",
                    "scope_names": scope,
                }, ensure_ascii=False),
            })
        return {"message": {"content": "已形成待确认教学设计。"}}


def test_retrieval_selection_validation_continues_to_teaching_design(session, monkeypatch):
    _taxonomy(session)
    monkeypatch.setattr(
        "calculus_agent.application.curriculum_retrieval.get_embedding_provider",
        lambda: LocalHashingEmbedding(dim=256),
    )
    backend = ScopeFlowBackend()

    result = run_teacher_agent(
        session,
        "学生极限不好，帮我设计复习方案",
        conversation_id="scope-full-flow",
        backend=backend,
    )

    assert result.status == "waiting_confirmation"
    assert result.teaching_design is not None
    assert result.teaching_design.content.scope_names == ["第一章 函数与极限"]
    assert backend.calls == 5
    assert not result.blocking_errors
    assert all(term not in result.message for term in (
        "CurriculumNode",
        "KnowledgeNode",
        "scope_level",
        "selected_node_ids",
    ))

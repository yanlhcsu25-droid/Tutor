from calculus_agent.models import KnowledgeNode, Question, QuestionDraft, QuestionKnowledgeLink
from calculus_agent.orchestration.agents import PaperAgentOrchestrator
from calculus_agent.orchestration.loop import ToolAgent
from calculus_agent.orchestration.types import AgentRunContext, AgentTool, RunBudget
from calculus_agent.schemas import PaperBlueprint


class FakeBackend:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[list[dict], list[dict]]] = []

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        self.requests.append((list(messages), list(tools)))
        if not self.responses:
            raise AssertionError("Fake backend has no response")
        return self.responses.pop(0)


def _text(content: str) -> dict:
    return {"message": {"role": "assistant", "content": content}}


def _call(name: str, arguments: dict) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
        }
    }


def _seed_question(session) -> None:
    knowledge = KnowledgeNode(
        node_type="concept",
        name="一次函数",
        normalized_name="一次函数",
        review_status="approved",
    )
    session.add(knowledge)
    draft = QuestionDraft(
        source_name="test",
        source_item_id="q1",
        variant=1,
        subject="初中数学",
        grade="八年级",
        question_type="解答题",
        question_text="已知 y=2x+1，求斜率。",
        reference_answers_json=["2"],
        normalized_fingerprint="1" * 64,
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        grade="八年级",
        question_type="解答题",
        final_answer="2",
        solution_json={"solution_steps": ["一次函数斜率为x的系数，所以答案为2。"]},
        verification_status="dataset_reference",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    session.add(
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge.id,
            relation_type="primary_concept",
        )
    )
    session.flush()


def test_tool_agent_records_disallowed_tool(session):
    backend = FakeBackend([_call("forbidden", {}), _text("已处理错误")])
    context = AgentRunContext(session=session, budget=RunBudget(max_steps=2))
    agent = ToolAgent(
        name="RestrictedAgent",
        system_prompt="restricted",
        backend=backend,
        tools=[],
    )
    result = agent.run("test", context)
    assert result.text == "已处理错误"
    assert context.traces[0].status == "error"
    assert "not allowed" in context.traces[0].result["error"]


def test_multi_agent_delegates_composes_and_reviews(session):
    _seed_question(session)
    blueprint = {
        "title": "八年级一次函数测试",
        "grade": "八年级",
        "total_questions": 1,
        "total_score": 10,
        "question_type_counts": {"解答题": 1},
        "knowledge_quotas": [{"name": "一次函数", "count": 1}],
        "seed": 42,
    }
    backend = FakeBackend([])
    context = AgentRunContext(session=session, budget=RunBudget(max_steps=8))
    result = PaperAgentOrchestrator(backend).run(
        "生成一套八年级一次函数测试卷",
        context,
        mode="multi_agent",
        blueprint=PaperBlueprint.model_validate(blueprint),
    )
    assert "组卷阶段：已生成 1 道题" in result.text
    assert "审核阶段：审核通过，无硬约束或答案解析问题" in result.text
    assert context.current_paper is not None
    assert context.current_paper.feasible is True
    assert [trace.actor for trace in context.traces] == [
        "KnowledgeStewardAgent",
        "PaperComposerAgent",
        "PaperReviewerAgent",
    ]
    assert context.traces[-1].tool_name == "validate_current_paper"
    assert context.traces[-1].result["status"] == "passed"


def test_multi_agent_requires_parsed_blueprint(session):
    backend = FakeBackend([])
    context = AgentRunContext(session=session, budget=RunBudget(max_steps=8))
    try:
        PaperAgentOrchestrator(backend).run("生成测试卷", context, mode="multi_agent")
    except ValueError as error:
        assert str(error) == "多 Agent 模式缺少已解析的组卷蓝图"
    else:
        raise AssertionError("missing blueprint must fail")


def test_repeated_identical_tool_call_is_blocked(session):
    echo = AgentTool(
        name="echo",
        description="echo",
        parameters={"type": "object", "properties": {}},
        handler=lambda arguments: arguments,
    )
    backend = FakeBackend(
        [_call("echo", {}), _call("echo", {}), _call("echo", {}), _text("stopped")]
    )
    context = AgentRunContext(session=session, budget=RunBudget(max_steps=5))
    ToolAgent(name="LoopAgent", system_prompt="test", backend=backend, tools=[echo]).run(
        "test", context
    )
    assert [trace.status for trace in context.traces] == ["success", "success", "error"]
    assert "Repeated identical" in context.traces[-1].result["error"]

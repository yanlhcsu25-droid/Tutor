"""Reinforcement-plan feature tests: deterministic service + tool + Agent routing.

V1 boundary (wrong question -> reinforcement evidence, NOT mastery diagnosis):
  - Python resolves PaperItems from structured feedback, never the LLM.
  - Knowledge aggregation / weight / scope are deterministic and unit-tested.
  - prepare_reinforcement_plan reuses the existing GenerationService + PendingGeneration.
  - confirm_generation is the only confirmation boundary; no second lifecycle.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
    PendingGeneration,
)
from calculus_agent.agent.paper_tool_registry import build_paper_tools
from calculus_agent.agent.schemas import (
    FeedbackItemInput,
    GeneratePaperInput,
    GenerationPlanPreview,
    PrepareReinforcementPlanInput,
)
from calculus_agent.agent.services.reinforcement import (
    ReinforcementContext,
    ReinforcementError,
    ReinforcementService,
    reinforcement_weight,
)
from calculus_agent.agent.tool_registry import AgentExecutionContext, EmptyInput
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    TeacherAgentRunTrace,
    Textbook,
)
from calculus_agent.papers.addressing import QuestionAddress


# ── lightweight model seed helpers ───────────────────────────────────────────

def _textbook(session) -> Textbook:
    tb = Textbook(id="tb-active", name="高等数学", is_active=True, directory_revision=1)
    session.add(tb)
    session.flush()
    return tb


def _chapter(session, tb, cid: str, title: str) -> CurriculumNode:
    node = CurriculumNode(
        id=cid,
        textbook_id=tb.id,
        parent_id=None,
        node_type="chapter",
        code=cid,
        title=title,
        sort_order=1 if "1" in cid else 3,
        source="teacher_directory",
        review_status="approved",
    )
    session.add(node)
    session.flush()
    return node


def _knode(session, kid: str, chapter_id: str, name: str, *, review_status: str = "approved") -> KnowledgeNode:
    node = KnowledgeNode(
        id=kid,
        curriculum_node_id=chapter_id,
        node_type="concept",
        name=name,
        normalized_name=name,
        review_status=review_status,
    )
    session.add(node)
    session.flush()
    return node


def _question(
    session,
    qid: str,
    qtype: str,
    chapter_id: str | None,
    knowledge_ids: list[str],
    *,
    profile_difficulty: int = 3,
) -> Question:
    draft = QuestionDraft(
        id=f"d-{qid}",
        source_name="seed",
        source_item_id=qid,
        variant=1,
        subject="高等数学",
        question_type=qtype,
        question_text=f"题目 {qid}",
        normalized_fingerprint="0" * 64,
        status="approved",
    )
    question = Question(
        id=qid,
        draft_id=draft.id,
        curriculum_chapter_id=chapter_id,
        question_text=draft.question_text,
        question_type=qtype,
        final_answer="1",
        solution_json={"solution_steps": ["参考解答步骤"]},
        verification_status="verified",
        review_status="approved",
    )
    session.add_all([draft, question])
    session.flush()
    for index, kid in enumerate(knowledge_ids or []):
        session.add(QuestionKnowledgeLink(
            question_id=qid,
            knowledge_node_id=kid,
            relation_type="primary" if index == 0 else "secondary",
        ))
    if knowledge_ids:
        session.add(QuestionProfile(
            question_id=qid,
            profile_version=1,
            difficulty=profile_difficulty,
            estimated_time_min=5,
            reasoning_depth=1,
            calculation_load=1,
            knowledge_depth=1,
            comprehensive_level=1,
            confidence=1,
            profile_source="human",
            profile_status="approved",
            reason="seed",
        ))
    session.flush()
    return question


def _paper(session, pid: str, title: str) -> Paper:
    blueprint = PaperBlueprintRecord(
        id=f"bp-{pid}",
        title=title,
        blueprint_json={},
        status="draft",
    )
    paper = Paper(
        id=pid,
        blueprint_id=blueprint.id,
        root_paper_id=pid,
        version=1,
        status="draft",
        title=title,
        total_score=100,
        validation_status="pending",
    )
    session.add_all([blueprint, paper])
    session.flush()
    return paper


def _item(session, paper: Paper, question: Question, position: int, *, score: float = 10) -> PaperItem:
    item = PaperItem(
        paper_id=paper.id,
        question_id=question.id,
        section=question.question_type,
        position=position,
        score=score,
        locked=False,
    )
    session.add(item)
    session.flush()
    return item


def _service(session, conversation_id: str) -> ReinforcementService:
    from calculus_agent.agent.services.generation import GenerationService

    generation_service = GenerationService(
        session=session,
        store=DatabasePendingReplacementStore(session),
        conversation_id=conversation_id,
    )
    return ReinforcementService(session=session, generation_service=generation_service)


def _feedback(address=None, position=None, note=None) -> FeedbackItemInput:
    return FeedbackItemInput(address=address, position=position, teacher_note=note)


# ── pure deterministic rule: weight ──────────────────────────────────────────

def test_reinforcement_weight_mapping():
    assert reinforcement_weight(1) == 3
    assert reinforcement_weight(2) == 4
    assert reinforcement_weight(3) == 5
    assert reinforcement_weight(7) == 5  # capped at 5
    assert reinforcement_weight(0) == 2  # guarded, never negative


# ── structured input validation (§36) ───────────────────────────────────────

def test_feedback_item_requires_address_or_position():
    with pytest.raises(ValidationError):
        FeedbackItemInput()  # neither
    with pytest.raises(ValidationError):
        FeedbackItemInput(address=QuestionAddress(section_type="选择题", section_order=1), position=1)  # both


def test_prepare_reinforcement_plan_rejects_model_invented_fields():
    with pytest.raises(ValidationError):
        PrepareReinforcementPlanInput.model_validate({
            "items": [{"address": {"section_type": "选择题", "section_order": 1}}],
            "knowledge_node_id": "k-x",  # forbidden
        })
    with pytest.raises(ValidationError):
        FeedbackItemInput.model_validate({
            "address": {"section_type": "选择题", "section_order": 1},
            "question_id": "q-x",  # forbidden
        })


# ── domain unit tests A–J (deterministic aggregation, no generation) ─────────

def test_case_a_single_question_single_knowledge(session):
    tb = _textbook(session)
    c1 = _chapter(session, tb, "c1", "第一章 函数与极限")
    ka = _knode(session, "ka", c1.id, "等价无穷小")
    p = _paper(session, "pa", "第一章测试卷")
    q = _question(session, "q1", "选择题", c1.id, [ka.id])
    _item(session, p, q, 1)
    svc = _service(session, "case-a")
    ctx = svc.build_context(p.id, [_feedback(address=QuestionAddress(section_type="选择题", section_order=1))])
    assert ctx.target_knowledge[0].knowledge_node_id == ka.id
    assert ctx.target_knowledge[0].evidence_count == 1
    assert ctx.target_knowledge[0].weight == 3
    assert ctx.scope_names == ["第一章 函数与极限"]


def test_case_b_repeated_knowledge_three_questions(session):
    tb = _textbook(session)
    c1 = _chapter(session, tb, "c1", "第一章 函数与极限")
    ka = _knode(session, "ka", c1.id, "等价无穷小")
    p = _paper(session, "pb", "第一章测试卷")
    for n in range(1, 4):
        q = _question(session, f"q{n}", "选择题", c1.id, [ka.id])
        _item(session, p, q, n)
    svc = _service(session, "case-b")
    items = [
        _feedback(address=QuestionAddress(section_type="选择题", section_order=n))
        for n in range(1, 4)
    ]
    ctx = svc.build_context(p.id, items)
    assert ctx.target_knowledge[0].evidence_count == 3
    assert ctx.target_knowledge[0].weight == 5


def test_case_c_multi_knowledge_aggregation(session):
    tb = _textbook(session)
    c1 = _chapter(session, tb, "c1", "第一章 函数与极限")
    ka = _knode(session, "ka", c1.id, "等价无穷小")
    kb = _knode(session, "kb", c1.id, "极限运算法则")
    p = _paper(session, "pc", "第一章测试卷")
    q1 = _question(session, "q1", "选择题", c1.id, [ka.id, kb.id])
    _item(session, p, q1, 1)
    q2 = _question(session, "q2", "计算题", c1.id, [kb.id])
    _item(session, p, q2, 2)
    svc = _service(session, "case-c")
    ctx = svc.build_context(p.id, [
        _feedback(address=QuestionAddress(section_type="选择题", section_order=1)),
        _feedback(address=QuestionAddress(section_type="计算题", section_order=1)),
    ])
    by_name = {t.knowledge_name: t for t in ctx.target_knowledge}
    assert by_name["等价无穷小"].evidence_count == 1
    assert by_name["等价无穷小"].weight == 3
    assert by_name["极限运算法则"].evidence_count == 2
    assert by_name["极限运算法则"].weight == 4


def test_case_d_duplicate_knowledge_link_counts_once(session):
    tb = _textbook(session)
    c1 = _chapter(session, tb, "c1", "第一章 函数与极限")
    ka = _knode(session, "ka", c1.id, "等价无穷小")
    p = _paper(session, "pd", "第一章测试卷")
    q = _question(session, "q1", "选择题", c1.id, [ka.id])
    # second link to the SAME knowledge node via a different relation type
    session.add(QuestionKnowledgeLink(question_id="q1", knowledge_node_id=ka.id, relation_type="method"))
    session.flush()
    _item(session, p, q, 1)
    svc = _service(session, "case-d")
    ctx = svc.build_context(p.id, [_feedback(address=QuestionAddress(section_type="选择题", section_order=1))])
    assert ctx.target_knowledge[0].evidence_count == 1
    assert ctx.target_knowledge[0].weight == 3


def test_case_e_duplicate_feedback_item_deduped(session):
    tb = _textbook(session)
    c1 = _chapter(session, tb, "c1", "第一章 函数与极限")
    ka = _knode(session, "ka", c1.id, "等价无穷小")
    p = _paper(session, "pe", "第一章测试卷")
    q = _question(session, "q1", "选择题", c1.id, [ka.id])
    _item(session, p, q, 1)
    svc = _service(session, "case-e")
    ctx = svc.build_context(p.id, [
        _feedback(address=QuestionAddress(section_type="选择题", section_order=1)),
        _feedback(address=QuestionAddress(section_type="选择题", section_order=1)),
    ])
    assert ctx.target_knowledge[0].evidence_count == 1
    assert "duplicate_feedback_reference_ignored" in ctx.warnings


def test_case_f_invalid_address_not_found(session):
    tb = _textbook(session)
    c1 = _chapter(session, tb, "c1", "第一章 函数与极限")
    ka = _knode(session, "ka", c1.id, "等价无穷小")
    p = _paper(session, "pf", "第一章测试卷")
    q = _question(session, "q1", "选择题", c1.id, [ka.id])
    _item(session, p, q, 1)
    svc = _service(session, "case-f")
    with pytest.raises(ReinforcementError) as exc:
        svc.build_context(p.id, [_feedback(address=QuestionAddress(section_type="选择题", section_order=99))])
    assert exc.value.code == "feedback_question_not_found"


def test_case_g_no_current_paper(session):
    svc = _service(session, "case-g")
    with pytest.raises(ReinforcementError) as exc:
        svc.build_context("does-not-exist", [_feedback(position=1)])
    assert exc.value.code == "no_current_paper"


def test_case_h_question_without_knowledge_unresolved(session):
    tb = _textbook(session)
    c1 = _chapter(session, tb, "c1", "第一章 函数与极限")
    p = _paper(session, "ph", "第一章测试卷")
    q = _question(session, "q1", "选择题", c1.id, [])  # no knowledge link
    _item(session, p, q, 1)
    svc = _service(session, "case-h")
    with pytest.raises(ReinforcementError) as exc:
        svc.build_context(p.id, [_feedback(address=QuestionAddress(section_type="选择题", section_order=1))])
    assert exc.value.code == "reinforcement_knowledge_unresolved"


def test_case_i_question_without_owning_chapter(session):
    tb = _textbook(session)
    c1 = _chapter(session, tb, "c1", "第一章 函数与极限")
    ka = _knode(session, "ka", c1.id, "等价无穷小")
    p = _paper(session, "pi", "第一章测试卷")
    # curriculum_chapter_id=None despite having a valid knowledge link
    q = _question(session, "q1", "选择题", None, [ka.id])
    _item(session, p, q, 1)
    svc = _service(session, "case-i")
    with pytest.raises(ReinforcementError) as exc:
        svc.build_context(p.id, [_feedback(address=QuestionAddress(section_type="选择题", section_order=1))])
    assert exc.value.code == "reinforcement_scope_unresolved"


def test_case_j_cross_chapter_scope(session):
    tb = _textbook(session)
    c1 = _chapter(session, tb, "c1", "第一章 函数与极限")
    c3 = _chapter(session, tb, "c3", "第三章 导数")
    ka = _knode(session, "ka", c1.id, "等价无穷小")
    kd = _knode(session, "kd", c3.id, "导数定义")
    p = _paper(session, "pj", "混合卷")
    q1 = _question(session, "q1", "选择题", c1.id, [ka.id])
    _item(session, p, q1, 1)
    q2 = _question(session, "q2", "计算题", c3.id, [kd.id])
    _item(session, p, q2, 2)
    svc = _service(session, "case-j")
    ctx = svc.build_context(p.id, [
        _feedback(address=QuestionAddress(section_type="选择题", section_order=1)),
        _feedback(address=QuestionAddress(section_type="计算题", section_order=1)),
    ])
    assert ctx.scope_names == ["第一章 函数与极限", "第三章 导数"]
    assert {t.knowledge_name for t in ctx.target_knowledge} == {"等价无穷小", "导数定义"}


# ── tool integration (§30, §31) ──────────────────────────────────────────────

def _rich_paper(session, pid: str = "paper-a") -> Paper:
    """Chapter-1 paper with 16 questions (4 per type) so generation is feasible."""
    tb = _textbook(session)
    c1 = _chapter(session, tb, "c1", "第一章 函数与极限")
    ka = _knode(session, "ka", c1.id, "等价无穷小")
    kb = _knode(session, "kb", c1.id, "极限运算法则")
    kc = _knode(session, "kc", c1.id, "连续")
    types = ["选择题", "填空题", "计算题", "证明题"]
    p = _paper(session, pid, "第一章测试卷")
    pos = 0
    for t in range(4):
        qtype = types[t]
        for n in range(4):
            pos += 1
            qid = f"{pid}-q{pos}"
            # Wrong-question anchors: 选择题第1题 -> ka, 计算题第1题 -> kb
            if qtype == "选择题" and n == 0:
                kids = [ka.id]
            elif qtype == "计算题" and n == 0:
                kids = [kb.id]
            else:
                kids = [kc.id]
            q = _question(session, qid, qtype, c1.id, kids)
            _item(session, p, q, pos)
    return p


def _make_context(session, paper_id: str, conversation_id: str) -> AgentExecutionContext:
    return AgentExecutionContext(
        session=session,
        conversation_id=conversation_id,
        paper_id=paper_id,
        version_id=paper_id,
        state_store=DatabasePendingReplacementStore(session),
        expected_pending_generation_version=None,
    )


def _paper_count(session) -> int:
    return session.scalar(select(func.count(Paper.id)))


def _trace_names(session, conversation_id: str) -> list[str]:
    trace = session.scalar(
        select(TeacherAgentRunTrace)
        .where(TeacherAgentRunTrace.conversation_id == conversation_id)
        .order_by(TeacherAgentRunTrace.id.desc())
    )
    calls = trace.tool_calls_json or [] if trace is not None else []
    return [call["tool_name"] for call in calls]


def test_tool_prepare_reinforcement_plan_waiting_confirmation(session):
    p = _rich_paper(session)
    ctx = _make_context(session, p.id, "conv-tool-1")
    tools = build_paper_tools(ctx)
    before = _paper_count(session)
    result = tools["prepare_reinforcement_plan"].execute(
        PrepareReinforcementPlanInput.model_validate({
            "items": [
                {"address": {"section_type": "选择题", "section_order": 1}},
                {"address": {"section_type": "计算题", "section_order": 1}},
            ]
        })
    )
    assert result.status == "waiting_confirmation"
    preview = result.result_fields["generation_preview"]
    assert isinstance(preview, GenerationPlanPreview) and preview.ok
    assert ctx.state_store.get_generation("conv-tool-1") is not None
    # No Paper was created by a preview.
    assert _paper_count(session) == before
    # Reinforcement context is machine-readable in the payload.
    rc = result.payload["reinforcement_context"]
    names = {t["knowledge_name"] for t in rc["target_knowledge"]}
    assert {"等价无穷小", "极限运算法则"} <= names


def test_tool_prepare_then_confirm_creates_paper_b(session):
    p = _rich_paper(session)
    ctx = _make_context(session, p.id, "conv-tool-2")
    tools = build_paper_tools(ctx)
    tools["prepare_reinforcement_plan"].execute(
        PrepareReinforcementPlanInput.model_validate({
            "items": [
                {"address": {"section_type": "选择题", "section_order": 1}},
                {"address": {"section_type": "计算题", "section_order": 1}},
            ]
        })
    )
    before = _paper_count(session)
    confirm = tools["confirm_generation"].execute(EmptyInput())
    assert confirm.status == "completed"
    assert _paper_count(session) == before + 1
    # Pending generation cleared after confirmation.
    assert ctx.state_store.get_generation("conv-tool-2") is None


def test_tool_no_current_paper_returns_failed(session):
    ctx = _make_context(session, None, "conv-tool-3")
    tools = build_paper_tools(ctx)
    result = tools["prepare_reinforcement_plan"].execute(
        PrepareReinforcementPlanInput.model_validate({
            "items": [{"position": 1}]
        })
    )
    assert result.status == "failed"
    assert "no_current_paper" in result.result_fields["blocking_errors"]


# ── Agent routing regression (§32, §33, §34, §35) ────────────────────────────

class _Backend:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, messages, tools):
        return self.responses.pop(0)


def _tool(name: str, arguments: str = "{}") -> dict:
    return {"message": {"tool_calls": [{
        "id": f"{name}-call",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }]}}


def _final(text: str) -> dict:
    return {"message": {"content": text}}


def test_agent_routes_feedback_to_prepare_reinforcement_plan(session):
    p = _rich_paper(session)
    result = run_teacher_agent(
        session,
        "选择题第1题和计算题第1题错了，再给他出一套针对性的巩固卷。",
        conversation_id="conv-agent-1",
        paper_id=p.id,
        version_id=p.id,
        backend=_Backend(
            _tool(
                "prepare_reinforcement_plan",
                '{"items":['
                '{"address":{"section_type":"选择题","section_order":1}},'
                '{"address":{"section_type":"计算题","section_order":1}}]}',
            ),
            _final("下一套会重点加强等价无穷小和极限运算法则，因为它们是这次错题的核心考点。"),
        ),
    )
    trace_names = _trace_names(session, "conv-agent-1")
    assert "prepare_reinforcement_plan" in trace_names
    assert "confirm_generation" not in trace_names
    # No TeachingDesign resurrection.
    for td in ("create_teaching_design", "search_teaching_design_history", "activate_teaching_design"):
        assert td not in trace_names
    assert result.status == "waiting_confirmation"
    assert result.generation_preview is not None and result.generation_preview.ok
    # Design intent preserved (not overwritten by a fixed final_text).
    assert "等价无穷小" in result.message
    # Pending generation exists.
    assert DatabasePendingReplacementStore(session).get_generation("conv-agent-1") is not None




def test_new_reinforcement_task_isolated_from_unrelated_pending_generation(session):
    """A fresh reinforcement task must not inherit an unrelated old generation plan.

    The same Paper + same feedback should compile to the same pending generation
    request regardless of whether the conversation previously had an unrelated
    pending plan. This checks outcome/state isolation rather than a fixed tool path.
    """
    paper = _rich_paper(session, pid="paper-pending-isolation")

    feedback = PrepareReinforcementPlanInput.model_validate({
        "items": [
            {
                "address": {
                    "section_type": "选择题",
                    "section_order": 1,
                }
            },
            {
                "address": {
                    "section_type": "计算题",
                    "section_order": 1,
                }
            },
        ]
    })

    # Baseline: prepare the reinforcement plan in a clean conversation.
    clean_conversation_id = "conv-reinforcement-clean"
    clean_context = _make_context(
        session,
        paper.id,
        clean_conversation_id,
    )
    clean_result = build_paper_tools(clean_context)[
        "prepare_reinforcement_plan"
    ].execute(feedback)

    assert clean_result.status == "waiting_confirmation"

    clean_pending = clean_context.state_store.get_generation(
        clean_conversation_id
    )
    assert clean_pending is not None

    # Dirty conversation: seed an unrelated old generation plan first.
    dirty_conversation_id = "conv-reinforcement-dirty"
    dirty_context = _make_context(
        session,
        paper.id,
        dirty_conversation_id,
    )
    dirty_context.state_store.set_generation(
        dirty_conversation_id,
        PendingGeneration(
            request=GeneratePaperInput(
                paper_type="chapter_test",
                scope_names=["第三章"],
                question_count=8,
                total_score=80,
            ),
            total_score_source="teacher_explicit",
        ),
    )

    dirty_result = build_paper_tools(dirty_context)[
        "prepare_reinforcement_plan"
    ].execute(feedback)

    assert dirty_result.status == "waiting_confirmation"

    dirty_pending = dirty_context.state_store.get_generation(
        dirty_conversation_id
    )
    assert dirty_pending is not None

    # Core regression contract:
    # an unrelated old pending generation must have zero influence on the new
    # reinforcement plan. Compare the resulting authoritative requests rather
    # than prescribing an internal tool-call sequence.
    assert (
        dirty_pending.request.model_dump(mode="json")
        == clean_pending.request.model_dump(mode="json")
    )

    # High-signal diagnostics if this regresses.
    assert dirty_pending.request.scope_names == clean_pending.request.scope_names
    assert "第三章" not in (dirty_pending.request.scope_names or [])
    assert dirty_pending.request.total_score == clean_pending.request.total_score
    assert (
        dirty_pending.request.question_count
        == clean_pending.request.question_count
    )
    assert (
        dirty_pending.request.question_type_requirements
        == clean_pending.request.question_type_requirements
    )
    assert (
        dirty_pending.request.knowledge_preferences
        == clean_pending.request.knowledge_preferences
    )
    assert (
        dirty_pending.request.knowledge_priority_weights
        == clean_pending.request.knowledge_priority_weights
    )


def test_agent_confirm_path_reuses_existing_generation(session):
    p = _rich_paper(session)
    ctx = _make_context(session, p.id, "conv-agent-2")
    # Pre-create the pending reinforcement plan via the tool (deterministic).
    build_paper_tools(ctx)["prepare_reinforcement_plan"].execute(
        PrepareReinforcementPlanInput.model_validate({
            "items": [
                {"address": {"section_type": "选择题", "section_order": 1}},
                {"address": {"section_type": "计算题", "section_order": 1}},
            ]
        })
    )
    before = _paper_count(session)
    result = run_teacher_agent(
        session,
        "就按这个出。",
        conversation_id="conv-agent-2",
        paper_id=p.id,
        version_id=p.id,
        backend=_Backend(_tool("confirm_generation", "{}"), _final("已生成巩固卷。")),
    )
    trace_names = _trace_names(session, "conv-agent-2")
    assert "confirm_generation" in trace_names
    assert "prepare_reinforcement_plan" not in trace_names
    assert result.status == "completed"
    assert _paper_count(session) == before + 1
    assert DatabasePendingReplacementStore(session).get_generation("conv-agent-2") is None


def test_mixed_valid_and_invalid_feedback_is_all_or_nothing(session):
    """One invalid feedback reference must abort the whole reinforcement plan."""

    paper = _rich_paper(
        session,
        pid="paper-reinforcement-atomicity",
    )

    conversation_id = "conv-reinforcement-atomicity"

    context = _make_context(
        session,
        paper.id,
        conversation_id,
    )

    assert context.state_store.get_generation(
        conversation_id
    ) is None

    before_paper_count = _paper_count(session)

    feedback = PrepareReinforcementPlanInput.model_validate({
        "items": [
            {
                # Valid reference.
                "address": {
                    "section_type": "选择题",
                    "section_order": 1,
                }
            },
            {
                # Invalid reference: the fixture does not contain
                # 计算题第99题.
                "address": {
                    "section_type": "计算题",
                    "section_order": 99,
                }
            },
        ]
    })

    result = build_paper_tools(context)[
        "prepare_reinforcement_plan"
    ].execute(feedback)

    # A teacher-facing invalid question reference is recoverable input,
    # not an infrastructure/system failure.
    assert result.status == "needs_clarification"

    # The machine-readable reason must remain observable.
    assert result.payload.get("code") == "feedback_question_not_found"

    # Core atomicity contract:
    # the valid first feedback item must NOT produce a partial generation plan.
    assert context.state_store.get_generation(
        conversation_id
    ) is None

    # Preparation/clarification must never create a Paper.
    assert _paper_count(session) == before_paper_count


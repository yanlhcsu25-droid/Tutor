from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
)
from calculus_agent.agent.tool_adapters.teaching_design import (
    CreateTeachingDesignInput,
    ReviseTeachingDesignInput,
    build_teaching_design_tools,
)
from calculus_agent.agent.tool_adapters.teaching_environment import (
    InspectCurriculumInput,
    InspectQuestionBankInput,
    build_environment_inspection_tools,
)
from calculus_agent.agent.tool_registry import AgentExecutionContext
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    Textbook,
)
from calculus_agent.teaching_design.service import TeachingDesignService


def _environment(session):
    textbook = Textbook(
        name="T3",
        edition="1",
        is_active=True,
    )
    session.add(textbook)
    session.flush()

    chapter = CurriculumNode(
        textbook_id=textbook.id,
        node_type="chapter",
        code="1",
        title="第一章",
        sort_order=1,
        review_status="approved",
    )
    session.add(chapter)
    session.flush()

    section = CurriculumNode(
        textbook_id=textbook.id,
        parent_id=chapter.id,
        node_type="section",
        code="1.1",
        title="1.1 极限",
        sort_order=2,
        review_status="approved",
    )
    session.add(section)
    session.flush()

    knowledge = KnowledgeNode(
        curriculum_node_id=section.id,
        node_type="concept",
        name="极限",
        normalized_name="极限",
        source_type="directory",
        confidence=1.0,
        review_status="approved",
    )
    session.add(knowledge)
    session.flush()

    draft = QuestionDraft(
        source_name="ocr_import",
        source_item_id="tool-contract-1",
        variant=1,
        subject="高等数学",
        question_type="计算题",
        question_text="计算极限",
        reference_answers_json=["1"],
        normalized_fingerprint="a" * 64,
        status="approved",
    )
    session.add(draft)
    session.flush()

    question = Question(
        draft_id=draft.id,
        curriculum_chapter_id=chapter.id,
        question_text=draft.question_text,
        question_type="计算题",
        final_answer="1",
        solution_json={"solution_steps": ["解析"]},
        verification_status="verified",
        review_status="approved",
        is_active=True,
        knowledge_match_status="current",
    )
    session.add(question)
    session.flush()

    session.add(
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge.id,
            relation_type="primary_concept",
            confidence=1.0,
        )
    )
    session.add(
        QuestionProfile(
            question_id=question.id,
            profile_version=1,
            difficulty=3,
            estimated_time_min=8,
            reasoning_depth=3,
            calculation_load=3,
            knowledge_depth=3,
            comprehensive_level=3,
            confidence=0.95,
            profile_source="human",
            profile_status="approved",
            reason="fixture",
        )
    )
    session.flush()


def _context(session):
    return AgentExecutionContext(
        session=session,
        conversation_id="env-contract",
        paper_id=None,
        version_id=None,
        state_store=DatabasePendingReplacementStore(session),
        owner_key="local_teacher",
        run_id="run-contract",
        user_message="帮我设计第一章复习。",
    )


def _design_payload(scope_names):
    return {
        "title": "第一章复习",
        "objective": "完成第一章复习与测评。",
        "scope_names": scope_names,
        "teaching_priorities": ["根据真实题库供给安排测评"],
    }


def test_create_design_is_recoverably_rejected_before_environment_observation(session):
    _environment(session)
    context = _context(session)
    tools = {
        tool.name: tool
        for tool in build_teaching_design_tools(context)
    }

    result = tools["create_teaching_design"].execute(
        CreateTeachingDesignInput(
            content=_design_payload(["第一章"])
        )
    )

    assert result.status == "completed"
    assert result.payload["ok"] is False
    assert result.payload["code"] == "teaching_design_evidence_required"
    assert (
        TeachingDesignService(session).get_active(
            owner_key="local_teacher",
            conversation_id="env-contract",
        )
        is None
    )


def test_inspection_registers_trusted_evidence_and_create_injects_it(session):
    _environment(session)
    context = _context(session)
    env_tools = {
        tool.name: tool
        for tool in build_environment_inspection_tools(context)
    }

    curriculum = env_tools["inspect_curriculum"].execute(
        InspectCurriculumInput(
            scope_names=["第一章"]
        )
    )
    aggregate = env_tools["inspect_question_bank"].execute(
        InspectQuestionBankInput(
            scope_names=["第一章"],
            detail_level="aggregate",
        )
    )
    assert curriculum.payload["ok"] is True
    assert aggregate.payload["ok"] is True

    design_tools = {
        tool.name: tool
        for tool in build_teaching_design_tools(context)
    }
    created = design_tools["create_teaching_design"].execute(
        CreateTeachingDesignInput(
            content=_design_payload(["第一章"])
        )
    )

    assert created.payload["ok"] is True
    design = created.result_fields["teaching_design"]
    assert {
        item.kind
        for item in design.content.evidence_refs
    } == {
        "curriculum_scope",
        "question_bank_aggregate",
    }
    assert all(
        item.observed_by_run_id == "run-contract"
        for item in design.content.evidence_refs
    )


def test_question_bank_detail_requires_prior_aggregate(session):
    _environment(session)
    context = _context(session)
    tools = {
        tool.name: tool
        for tool in build_environment_inspection_tools(context)
    }

    result = tools["inspect_question_bank"].execute(
        InspectQuestionBankInput(
            scope_names=["第一章"],
            detail_level="chapter_detail",
            chapter_name="第一章",
        )
    )

    assert result.status == "completed"
    assert result.payload["ok"] is False
    assert result.payload["code"] == "question_bank_aggregate_required"


def test_environment_inspection_budget_is_bounded(session):
    _environment(session)
    context = _context(session)
    tools = {
        tool.name: tool
        for tool in build_environment_inspection_tools(context)
    }

    for _ in range(4):
        result = tools["inspect_curriculum"].execute(
            InspectCurriculumInput(
                scope_names=["第一章"]
            )
        )
        assert result.payload["ok"] is True

    fifth = tools["inspect_curriculum"].execute(
        InspectCurriculumInput(
            scope_names=["第一章"]
        )
    )
    assert fifth.payload["ok"] is False
    assert (
        fifth.payload["code"]
        == "environment_inspection_budget_exhausted"
    )


def test_untrusted_model_supplied_evidence_is_rejected(session):
    _environment(session)
    context = _context(session)
    env_tools = {
        tool.name: tool
        for tool in build_environment_inspection_tools(context)
    }
    env_tools["inspect_curriculum"].execute(
        InspectCurriculumInput(scope_names=["第一章"])
    )
    env_tools["inspect_question_bank"].execute(
        InspectQuestionBankInput(
            scope_names=["第一章"],
            detail_level="aggregate",
        )
    )

    design_tools = {
        tool.name: tool
        for tool in build_teaching_design_tools(context)
    }
    payload = _design_payload(["第一章"])
    payload["evidence_refs"] = [
        {
            "kind": "question_bank_aggregate",
            "ref_id": "fabricated:evidence",
            "summary": "模型自己编造的证据",
            "observed_by_run_id": "fake-run",
        }
    ]

    result = design_tools["create_teaching_design"].execute(
        CreateTeachingDesignInput(content=payload)
    )

    assert result.payload["ok"] is False
    assert result.payload["code"] == "untrusted_teaching_design_evidence"


def test_scope_change_requires_fresh_environment_evidence(session):
    _environment(session)
    context = _context(session)
    env_tools = {
        tool.name: tool
        for tool in build_environment_inspection_tools(context)
    }
    env_tools["inspect_curriculum"].execute(
        InspectCurriculumInput(scope_names=["第一章"])
    )
    env_tools["inspect_question_bank"].execute(
        InspectQuestionBankInput(
            scope_names=["第一章"],
            detail_level="aggregate",
        )
    )

    design_tools = {
        tool.name: tool
        for tool in build_teaching_design_tools(context)
    }
    created = design_tools["create_teaching_design"].execute(
        CreateTeachingDesignInput(
            content=_design_payload(["第一章"])
        )
    )
    assert created.payload["ok"] is True

    revised = design_tools["revise_teaching_design"].execute(
        ReviseTeachingDesignInput(
            patch={"scope_names": ["第二章"]},
            change_reason="teacher_added_new_scope",
        )
    )

    assert revised.payload["ok"] is False
    assert revised.payload["code"] == "teaching_design_evidence_required"
    active = TeachingDesignService(session).get_active(
        owner_key="local_teacher",
        conversation_id="env-contract",
    )
    assert active.version == 1

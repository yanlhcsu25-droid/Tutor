from unittest.mock import patch

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.agent.schemas import GeneratePaperInput, QuestionTypeRequirement
from calculus_agent.questions.chapter_assignment import (
    chapter_display_name,
    sync_question_chapter_ownership,
)
from calculus_agent.agent.tools.paper_tools import (
    GeneratePaperToolResult,
    PaperSummary,
    build_structured_generation_request,
    generate_paper_from_input,
)
from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    Paper,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
)


class _Backend:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, messages, tools):
        return self.responses.pop(0)


def _tool(arguments: str, name: str = "prepare_generation_plan") -> dict:
    return {
        "message": {
            "tool_calls": [{"id": "generate-call", "type": "function", "function": {"name": name, "arguments": arguments}}]
        }
    }


def _final(text: str = "已完成组卷。") -> dict:
    return {"message": {"content": text}}


def _scope(session):
    chapter = CurriculumNode(
        id="structured-chapter",
        node_type="chapter",
        code="1",
        title="函数与极限",
        sort_order=1,
    )
    session.add(chapter)
    session.add_all([
        KnowledgeNode(
            id="structured-limit",
            node_type="concept",
            name="函数极限",
            normalized_name="函数极限",
            curriculum_node_id=chapter.id,
        ),
        KnowledgeNode(
            id="structured-law",
            node_type="concept",
            name="极限运算法则",
            normalized_name="极限运算法则",
            curriculum_node_id=chapter.id,
        ),
        KnowledgeNode(
            id="structured-small",
            node_type="concept",
            name="无穷小",
            normalized_name="无穷小",
            curriculum_node_id=chapter.id,
        ),
    ])
    session.flush()
    return chapter


def _candidate(
    session,
    *,
    number: int,
    question_type: str,
    knowledge_id: str,
    with_answer: bool = False,
    with_solution: bool = False,
):
    draft = QuestionDraft(
        source_name="structured-generation",
        source_item_id=str(number),
        variant=1,
        subject="高数",
        question_type=question_type,
        question_text=f"结构化组卷题目{number}",
        normalized_fingerprint=f"{number:064d}",
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        question_type=question_type,
        final_answer=f"答案{number}" if with_answer else None,
        solution_json=(
            {"solution_steps": [f"解析{number}"]}
            if with_answer or with_solution else {}
        ),
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    session.add_all([
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge_id,
            relation_type="primary",
        ),
        QuestionProfile(
            question_id=question.id,
            profile_version=1,
            difficulty=3,
            estimated_time_min=5,
            reasoning_depth=1,
            calculation_load=1,
            knowledge_depth=1,
            comprehensive_level=1,
            confidence=1,
            profile_source="human",
            profile_status="approved",
            reason="structured generation test",
        ),
    ])
    session.flush()
    sync_question_chapter_ownership(session, question.id)


def _complex_input(**updates) -> GeneratePaperInput:
    values = dict(
        paper_type="chapter_exercise",
        scope_names=["函数与极限"],
        audience="大一",
        question_count=10,
        total_score=100,
        knowledge_preferences=["函数极限", "极限运算法则", "无穷小"],
        question_type_requirements=[
            QuestionTypeRequirement(question_type="选择题", count=2, score_each=5),
            QuestionTypeRequirement(question_type="填空题", count=1, score_each=5),
            QuestionTypeRequirement(question_type="计算题", count=5, score_each=13),
            QuestionTypeRequirement(question_type="证明题", count=2, score_each=10),
        ],
        difficulty_preference="从基础逐渐过渡到需要一定思考",
        diversity_preference="避免大量重复套路题",
    )
    values.update(updates)
    return GeneratePaperInput(**values)


def test_complex_structured_input_builds_existing_blueprint_without_raw_parser(session):
    _scope(session)
    request, warnings, errors, questions = build_structured_generation_request(
        session, _complex_input()
    )
    assert not errors and not questions
    assert request.blueprint.total_questions == 10
    assert request.blueprint.total_score == 100
    assert request.blueprint.question_type_counts == {
        "选择题": 2, "填空题": 1, "计算题": 5, "证明题": 2,
    }
    assert request.blueprint.soft_knowledge_preferences == ["函数极限", "极限运算法则", "无穷小"]
    assert request.constraints.scope_node_ids == [
        "structured-law", "structured-limit", "structured-small",
    ]
    assert request.constraints.preferred_knowledge_node_ids == [
        "structured-limit", "structured-law", "structured-small",
    ]
    assert request.constraints.audience == "大一"
    assert "difficulty_progression_is_soft" in warnings
    assert "diversity_preference_is_soft" in warnings


def test_python_recomputes_section_score_total(session):
    _scope(session)
    request, _, errors, _ = build_structured_generation_request(session, _complex_input())
    assert not errors
    assert sum(section.count * section.score_per_question for section in request.blueprint.sections) == 100


def test_complex_structured_input_reaches_real_draft_executor(session):
    _scope(session)
    question_types = ["选择题"] * 2 + ["填空题"] + ["计算题"] * 5 + ["证明题"] * 2
    knowledge_ids = ["structured-limit", "structured-law", "structured-small"]
    for number, question_type in enumerate(question_types, 1):
        _candidate(
            session,
            number=number,
            question_type=question_type,
            knowledge_id=knowledge_ids[(number - 1) % len(knowledge_ids)],
        )
    session.flush()
    result = generate_paper_from_input(session, _complex_input())
    assert result.ok is False
    assert "paper_validation_failed" in result.blocking_errors
    assert result.summary.total_questions == 10
    assert result.summary.total_score == 100
    assert result.summary.question_type_counts == {
        "选择题": 2, "填空题": 1, "计算题": 5, "证明题": 2,
    }
    paper = session.get(Paper, result.paper_id)
    assert paper.version == 1
    assert paper.validation_status == "failed"
    assert result.validation_status == "failed"
    assert result.validation_report.passed is False
    assert {item.code for item in result.validation_report.violations} == {
        "SOLUTION_MISSING",
    }
    assert "paper_validation_failed" in result.warnings


def test_structured_generation_returns_passed_validation_report(session):
    _scope(session)
    question_types = ["选择题"] * 2 + ["填空题"] + ["计算题"] * 5 + ["证明题"] * 2
    knowledge_ids = ["structured-limit", "structured-law", "structured-small"]
    for number, question_type in enumerate(question_types, 1):
        _candidate(
            session,
            number=number,
            question_type=question_type,
            knowledge_id=knowledge_ids[(number - 1) % len(knowledge_ids)],
            with_answer=True,
        )
    session.flush()

    result = generate_paper_from_input(session, _complex_input())

    assert result.ok
    assert result.validation_status == "passed"
    assert result.validation_report.passed is True
    assert result.validation_report.violations == []
    assert session.get(Paper, result.paper_id).validation_status == "passed"
    assert "paper_validation_failed" not in result.warnings


def test_structured_generation_accepts_solution_without_final_answer(session):
    _scope(session)
    question_types = ["选择题"] * 2 + ["填空题"] + ["计算题"] * 5 + ["证明题"] * 2
    knowledge_ids = ["structured-limit", "structured-law", "structured-small"]
    for number, question_type in enumerate(question_types, 1):
        _candidate(
            session,
            number=number,
            question_type=question_type,
            knowledge_id=knowledge_ids[(number - 1) % len(knowledge_ids)],
            with_solution=True,
        )
    session.flush()

    result = generate_paper_from_input(session, _complex_input())

    assert result.ok
    assert result.validation_status == "passed"
    assert result.validation_report.passed is True
    assert result.validation_report.violations == []
    assert session.get(Paper, result.paper_id).validation_status == "passed"
    assert "paper_validation_failed" not in result.warnings


def test_structured_generation_rejects_score_total_mismatch(session):
    _scope(session)
    request, _, errors, questions = build_structured_generation_request(
        session, _complex_input(total_score=95)
    )
    assert request is None
    assert errors == ["score_total_mismatch"]
    assert "100" in questions[0] and "95" in questions[0]


def test_structured_generation_derives_question_count_from_complete_type_counts(session):
    _scope(session)
    request, _, errors, _ = build_structured_generation_request(
        session, _complex_input(question_count=9)
    )
    assert errors == []
    assert request.blueprint.total_questions == 10


def test_scope_and_knowledge_names_resolve_to_database_facts(session):
    _scope(session)
    request, _, errors, _ = build_structured_generation_request(session, _complex_input())
    assert not errors
    assert set(request.constraints.scope_node_ids) == {
        "structured-limit", "structured-law", "structured-small",
    }
    assert request.constraints.preferred_knowledge_node_ids == [
        "structured-limit", "structured-law", "structured-small",
    ]


def test_generation_accepts_curriculum_display_scope_contract(session):
    """A curriculum-inspection display label must be valid generation input.

    This locks the real integration contract that failed in manual acceptance:
    inspect_curriculum emits chapter_display_name(...), and the same string is
    then reused by inspect_question_bank / prepare_generation_plan.
    """
    chapter = _scope(session)
    display_scope = chapter_display_name(chapter)
    assert display_scope

    request, _, errors, questions = build_structured_generation_request(
        session,
        _complex_input(scope_names=[display_scope]),
    )

    assert errors == []
    assert questions == []
    assert request is not None
    assert set(request.constraints.scope_node_ids) == {
        "structured-limit", "structured-law", "structured-small",
    }


def test_missing_knowledge_name_returns_structured_error(session):
    _scope(session)
    request, _, errors, questions = build_structured_generation_request(
        session, _complex_input(knowledge_preferences=["不存在的知识点"])
    )
    assert request is None
    assert errors == ["knowledge_unknown"]
    assert questions
    assert "不存在的知识点" in questions[0]


def test_llm_generation_request_creates_preview_without_composing(session):
    _scope(session)
    arguments = '{"paper_type":"chapter_test","scope_names":["第一章"]}'
    backend = _Backend(_tool(arguments), _final())
    with patch("calculus_agent.agent.services.generation.generate_paper_from_input") as tool:
        result = run_teacher_agent(session, "来套第一章练习", backend=backend)
    assert result.status == "waiting_confirmation"
    assert result.generation_preview and result.generation_preview.total_score == 100
    tool.assert_not_called()


def test_multiturn_generation_preview_then_confirmation(session):
    _scope(session)
    first = run_teacher_agent(
        session,
        "第一章练习题",
        conversation_id="structured-multiturn",
        backend=_Backend(
            _tool('{"paper_type":"chapter_exercise","scope_names":["第一章"]}'),
            _final("请确认方案。"),
        ),
    )
    assert first.status == "waiting_confirmation"
    backend = _Backend(
        _tool('{}', "confirm_generation"),
        _final(),
    )
    success = GeneratePaperToolResult(
        ok=True,
        summary=PaperSummary(total_questions=5, total_score=50),
    )
    with (
        patch("calculus_agent.agent.services.generation.generate_paper_from_input", return_value=success) as tool,
    ):
        second = run_teacher_agent(
            session,
            "是的",
            conversation_id="structured-multiturn",
            backend=backend,
        )
    assert second.status == "completed"
    assert tool.call_args.args[1].scope_names == ["第一章"]


def test_missing_model_does_not_fall_back_to_hardcoded_intent_routing(session):
    result = run_teacher_agent(session, "帮我出一套第一章测试卷")
    assert result.status == "failed"
    assert result.blocking_errors == ["agent_model_unavailable"]


def test_generation_patch_preserves_authoritative_pending_scope(session):
    _scope(session)
    conversation_id = "generation-memory-merge"
    first = run_teacher_agent(
        session, "第一章测试卷", conversation_id=conversation_id,
        backend=_Backend(_tool('{"paper_type":"chapter_test","scope_names":["第一章"]}'), _final("请确认")),
    )
    second = run_teacher_agent(
        session, "选择题改成1道", conversation_id=conversation_id,
        backend=_Backend(_tool('{"question_type_patches":[{"question_type":"选择题","count":1}]}'), _final("已更新")),
    )
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    assert first.status == second.status == "waiting_confirmation"
    assert pending.request.scope_names == ["第一章"]
    assert pending.request.question_type_requirements[0].count == 1


def test_default_generation_is_normalized_into_authoritative_pending(session):
    _scope(session)
    conversation_id = "normalized-pending"
    run_teacher_agent(
        session, "第一章测试卷", conversation_id=conversation_id,
        backend=_Backend(_tool('{"paper_type":"chapter_test","scope_names":["第一章"]}'), _final("请确认")),
    )
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    assert pending.request.total_score == 100
    assert pending.request.question_count == 10
    assert [(item.question_type, item.count, item.score_each) for item in pending.request.question_type_requirements] == [
        ("选择题", 4, 5), ("填空题", 2, 10), ("计算题", 4, 15),
    ]


def test_count_only_patch_recomputes_default_total_score(session):
    """Problem 2: default-source count change keeps each score_each and recomputes
    the plan total (no hard 100 constraint, no rebalance ambiguity)."""
    _scope(session)
    conversation_id = "score-rebalance"
    run_teacher_agent(
        session, "第一章测试卷", conversation_id=conversation_id,
        backend=_Backend(_tool('{"paper_type":"chapter_test","scope_names":["第一章"]}'), _final("请确认")),
    )
    result = run_teacher_agent(
        session, "选择题改成2道", conversation_id=conversation_id,
        backend=_Backend(_tool('{"question_type_patches":[{"question_type":"选择题","count":2}]}'), _final("已更新")),
    )
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    scores = {item.question_type: item.score_each for item in pending.request.question_type_requirements}
    assert result.status == "waiting_confirmation"
    assert result.blocking_errors == []
    assert pending.request.total_score == 90
    assert pending.request.question_count == 8
    assert scores["计算题"] == 15
    assert scores["填空题"] == 10
    assert scores["选择题"] == 5


def test_direct_generation_forces_blueprint_card_after_env_inspection(session):
    """Problem 1: a direct new-paper request that inspected the environment must
    keep going in the same Agent turn and call prepare_generation_plan (the
    editable blueprint card), instead of stopping at a natural-language proposal.
    """
    _scope(session)
    conversation_id = "direct-gen-blueprint"
    backend = _Backend(
        _tool('{"scope_names":["第一章"]}', "inspect_curriculum"),
        _tool('{"scope_names":["第一章"]}', "inspect_question_bank"),
        _final("建议方案：选择题4道、填空题2道、计算题4道，请确认。"),
        _tool('{"paper_type":"chapter_test","scope_names":["第一章"]}', "prepare_generation_plan"),
        _final(
            "组卷设计意图：本套试卷以函数极限基础为主线，重点检查学生对核心方法的掌握情况；"
            "具体题型与分值请在下方蓝图中调整。"
        ),
    )
    with patch("calculus_agent.agent.services.generation.generate_paper_from_input") as gen:
        result = run_teacher_agent(
            session, "帮我出一套第一章测试卷", conversation_id=conversation_id, backend=backend
        )
    # The blueprint (preview) is produced without composing the paper.
    gen.assert_not_called()
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    assert result.status == "waiting_confirmation"
    assert result.generation_preview is not None
    assert result.generation_preview.total_score == 100
    assert pending is not None
    assert pending.request.scope_names == ["第一章"]
    assert result.message == (
        "组卷设计意图：本套试卷以函数极限基础为主线，重点检查学生对核心方法的掌握情况；"
        "具体题型与分值请在下方蓝图中调整。"
    )

    # Subsequent turn: a single confirm executes generation.
    confirm_backend = _Backend(_tool('{}', "confirm_generation"), _final("已生成。"))
    success = GeneratePaperToolResult(
        ok=True, summary=PaperSummary(total_questions=10, total_score=100)
    )
    with patch(
        "calculus_agent.agent.services.generation.generate_paper_from_input",
        return_value=success,
    ) as gen:
        second = run_teacher_agent(
            session, "确认生成", conversation_id=conversation_id, backend=confirm_backend
        )
    assert second.status == "completed"
    gen.assert_called_once()
    assert gen.call_args.args[1].scope_names == ["第一章"]



def test_new_generation_does_not_expose_new_teaching_design_tools(session):
    # Fresh generation requests must not regain the retired TeachingDesign entrypoint.
    _scope(session)

    class _RecordingBackend(_Backend):
        def __init__(self, *responses):
            super().__init__(*responses)
            self.tool_name_sets = []

        def complete(self, messages, tools):
            self.tool_name_sets.append({
                item["function"]["name"]
                for item in tools
                if isinstance(item, dict)
                and isinstance(item.get("function"), dict)
                and item["function"].get("name")
            })
            return super().complete(messages, tools)

    backend = _RecordingBackend(
        _tool(
            '{"paper_type":"chapter_test","scope_names":["第一章"]}',
            "prepare_generation_plan",
        ),
        _final(
            "组卷设计意图：本套试卷用于检查第一章核心知识的掌握情况，"
            "具体题型与分值请在下方蓝图中调整。"
        ),
    )

    with patch("calculus_agent.agent.services.generation.generate_paper_from_input") as gen:
        result = run_teacher_agent(
            session,
            "学生函数极限基础还可以，但是等价无穷小掌握不好，给他出一套第一章巩固卷。",
            conversation_id="no-new-teaching-design-tools",
            backend=backend,
        )

    gen.assert_not_called()
    assert result.status == "waiting_confirmation"
    assert result.generation_preview is not None
    assert result.message.startswith("组卷设计意图：")

    forbidden = {
        "create_teaching_design",
        "search_teaching_design_history",
        "activate_teaching_design",
    }
    assert backend.tool_name_sets
    assert all(
        forbidden.isdisjoint(names)
        for names in backend.tool_name_sets
    )


def test_teacher_explicit_total_score_rebalanced_on_count_change(session):
    """Problem 2 rule A: teacher explicitly owns total_score=100. A count change
    deterministically rebalances and keeps the final total at 100 (no ambiguity).
    """
    _scope(session)
    conversation_id = "explicit-rebalance"
    first = run_teacher_agent(
        session, "第一章测试卷，总分100", conversation_id=conversation_id,
        backend=_Backend(
            _tool('{"paper_type":"chapter_test","scope_names":["第一章"],"total_score":100}'),
            _final("请确认"),
        ),
    )
    assert first.status == "waiting_confirmation"
    pending_before = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    assert pending_before.total_score_source == "teacher_explicit"

    result = run_teacher_agent(
        session, "选择题改成2道", conversation_id=conversation_id,
        backend=_Backend(
            _tool('{"question_type_patches":[{"question_type":"选择题","count":2}]}'),
            _final("已平衡"),
        ),
    )
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    types = {item.question_type: item for item in pending.request.question_type_requirements}
    scores = {item.question_type: item.score_each for item in pending.request.question_type_requirements}
    assert result.status == "waiting_confirmation"
    assert "score_rebalance_ambiguous" not in result.blocking_errors
    assert pending.request.total_score == 100
    assert pending.request.question_count == 8
    # 选择题 count changed (4->2); its score_each is preserved.
    assert types["选择题"].count == 2 and types["选择题"].score_each == 5
    # Deterministic rebalance adjusted exactly one unlocked type to keep total 100.
    assert scores["填空题"] != 10 or scores["计算题"] != 15


def test_default_count_change_preserves_unmodified_fields(session):
    """Problem 2 rule B + scenario 4: default-source count change recomputes the
    total but preserves every unmodified field (scope, difficulty, other types).
    """
    _scope(session)
    conversation_id = "preserve-fields"
    run_teacher_agent(
        session, "第一章测试卷", conversation_id=conversation_id,
        backend=_Backend(
            _tool('{"paper_type":"chapter_test","scope_names":["第一章"],"difficulty_preference":"从基础逐渐过渡到需要思考"}'),
            _final("请确认"),
        ),
    )
    result = run_teacher_agent(
        session, "填空题改成3道", conversation_id=conversation_id,
        backend=_Backend(
            _tool('{"question_type_patches":[{"question_type":"填空题","count":3}]}'),
            _final("已更新"),
        ),
    )
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    types = {item.question_type: item for item in pending.request.question_type_requirements}
    assert result.status == "waiting_confirmation"
    assert pending.request.scope_names == ["第一章"]
    assert pending.request.difficulty_preference == "从基础逐渐过渡到需要思考"
    assert types["选择题"].count == 4 and types["选择题"].score_each == 5
    assert types["计算题"].count == 4 and types["计算题"].score_each == 15
    assert types["填空题"].count == 3 and types["填空题"].score_each == 10
    # 填空 2->3: total = 20 + 3*10 + 60 = 110 (recomputed, not forced to 100).
    assert pending.request.total_score == 110
    assert pending.total_score_source == "default_template"


def test_pending_rejects_full_question_type_restatement(session):
    _scope(session)
    conversation_id = "reject-full-restatement"
    run_teacher_agent(
        session, "第一章测试卷", conversation_id=conversation_id,
        backend=_Backend(_tool('{"paper_type":"chapter_test","scope_names":["第一章"]}'), _final("请确认")),
    )
    result = run_teacher_agent(
        session, "选择题改成1道，其他不变", conversation_id=conversation_id,
        backend=_Backend(_tool('{"question_type_requirements":[{"question_type":"选择题","count":1},{"question_type":"填空题","count":2},{"question_type":"计算题","count":5}]}'), _final("请使用局部修改")),
    )
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    assert "generation_partial_patch_required" in result.blocking_errors
    assert [(item.question_type, item.count) for item in pending.request.question_type_requirements] == [
        ("选择题", 4), ("填空题", 2), ("计算题", 4),
    ]


def test_successful_patch_retry_clears_recovered_error_from_result(session):
    _scope(session)
    conversation_id = "patch-retry-recovery"
    run_teacher_agent(
        session, "第一章测试卷", conversation_id=conversation_id,
        backend=_Backend(_tool('{"paper_type":"chapter_test","scope_names":["第一章"]}'), _final("请确认")),
    )
    result = run_teacher_agent(
        session, "选择题改成2道", conversation_id=conversation_id,
        backend=_Backend(
            _tool('{"question_type_requirements":[{"question_type":"选择题","count":2}]}'),
            _tool('{"question_type_patches":[{"question_type":"选择题","count":2}]}'),
            _final("已更新"),
        ),
    )
    assert result.status == "waiting_confirmation"
    assert result.blocking_errors == []
    assert result.clarification_questions == []


def test_rebalance_clarification_keeps_previous_pending(session):
    _scope(session)
    conversation_id = "score-rebalance-clarification"
    run_teacher_agent(
        session,
        (
            "第一章测试卷，总分100，"
            "选择题4道每题5分，"
            "填空题2道每题10分，"
            "计算题4道每题15分，"
            "这些分值都按我说的"
        ),
        conversation_id=conversation_id,
        backend=_Backend(_tool('{"paper_type":"chapter_test","scope_names":["第一章"],"total_score":100,"question_type_requirements":[{"question_type":"选择题","count":4,"score_each":5},{"question_type":"填空题","count":2,"score_each":10},{"question_type":"计算题","count":4,"score_each":15}]}'), _final("请确认")),
    )
    result = run_teacher_agent(
        session, "选择题改成1道", conversation_id=conversation_id,
        backend=_Backend(_tool('{"question_type_patches":[{"question_type":"选择题","count":1}]}'), _final("需要确认分值")),
    )
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    assert "score_rebalance_ambiguous" in result.blocking_errors
    assert pending.request.question_type_requirements[0].count == 4
    assert pending.request.total_score == 100


def test_explicit_new_total_score_is_preserved(session):
    _scope(session)
    conversation_id = "explicit-target-score"
    run_teacher_agent(
        session, "第一章测试卷", conversation_id=conversation_id,
        backend=_Backend(_tool('{"paper_type":"chapter_test","scope_names":["第一章"]}'), _final("请确认")),
    )
    result = run_teacher_agent(
        session, "选择题改成1道，总分改成85", conversation_id=conversation_id,
        backend=_Backend(_tool('{"total_score":85,"question_type_patches":[{"question_type":"选择题","count":1}]}'), _final("已更新")),
    )
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    assert result.status == "waiting_confirmation"
    assert pending.request.total_score == 85
    assert sum(item.count * item.score_each for item in pending.request.question_type_requirements) == 85


def test_unsupported_previous_paper_exclusion_is_remembered_not_executed(session):
    _scope(session)
    conversation_id = "unsupported-exclusion"
    result = run_teacher_agent(
        session, "第一章，再来一套但别和上一套重复", conversation_id=conversation_id,
        backend=_Backend(_tool('{"paper_type":"chapter_test","scope_names":["第一章"],"avoid_previous_paper_questions":true}'), _final("当前不能保证排重")),
    )
    store = DatabasePendingReplacementStore(session)
    memory = store.get_memory(conversation_id)
    pending = store.get_generation(conversation_id)
    assert result.status == "waiting_confirmation"
    assert "avoid_previous_paper_questions_unsupported" in result.warnings
    assert memory.unsupported_preferences[0]["status"] == "unsupported"
    assert "avoid_previous_paper_questions" not in pending.request.model_dump()
    assert "不能保证题目不重复" in result.message


def test_clarification_answer_completes_working_memory_draft(session):
    _scope(session)
    conversation_id = "clarification-memory"
    first = run_teacher_agent(
        session, "出一套章节测试", conversation_id=conversation_id,
        backend=_Backend(_tool('{"paper_type":"chapter_test"}'), _final("请确认范围")),
    )
    second = run_teacher_agent(
        session, "第一章，知识点不限", conversation_id=conversation_id,
        backend=_Backend(_tool('{"scope_names":["第一章"],"knowledge_preferences":[]}'), _final("请确认方案")),
    )
    pending = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    assert first.status == "needs_clarification"
    assert second.status == "waiting_confirmation"
    assert pending.request.paper_type == "chapter_test"
    assert pending.request.scope_names == ["第一章"]
    assert pending.request.knowledge_preferences == []

def test_model_default_total_score_does_not_become_teacher_explicit(session):
    """A model-added default 100 is not teacher provenance.

    The teacher did not mention a total score, so the Agent adapter must strip
    total_score from the Tool Call before GenerationService sees it. A later
    count-only patch therefore recomputes the default total instead of preserving
    100 as a hard constraint.
    """
    _scope(session)
    conversation_id = "model-default-score-provenance"

    first = run_teacher_agent(
        session,
        "第一章测试卷",
        conversation_id=conversation_id,
        backend=_Backend(
            _tool(
                '{"paper_type":"chapter_test","scope_names":["第一章"],'
                '"total_score":100}'
            ),
            _final("方案。"),
        ),
    )
    assert first.status == "waiting_confirmation"

    store = DatabasePendingReplacementStore(session)
    pending = store.get_generation(conversation_id)
    assert pending is not None
    assert pending.total_score_source == "default_template"
    assert pending.request.total_score == 100

    second = run_teacher_agent(
        session,
        "选择题改成2道",
        conversation_id=conversation_id,
        backend=_Backend(
            _tool(
                '{"question_type_patches":['
                '{"question_type":"选择题","count":2}'
                ']}'
            ),
            _final("已更新。"),
        ),
    )
    pending = store.get_generation(conversation_id)
    assert second.status == "waiting_confirmation"
    assert pending.total_score_source == "default_template"
    assert pending.request.total_score == 90
    assert "score_rebalance_ambiguous" not in second.blocking_errors


def test_environment_information_query_does_not_force_generation(session):
    """Environment inspection is not itself evidence of generation intent."""
    _scope(session)
    conversation_id = "environment-info-only"
    backend = _Backend(
        _tool('{"scope_names":["第一章"]}', "inspect_curriculum"),
        _tool(
            '{"scope_names":["第一章"],"detail_level":"aggregate"}',
            "inspect_question_bank",
        ),
        _final("第一章题库情况已经查到。"),
        # The post-inspection semantic recheck may answer without any Tool Call.
        _final("第一章题库情况已经查到。"),
    )

    result = run_teacher_agent(
        session,
        "帮我看看第一章现在题库供给怎么样",
        conversation_id=conversation_id,
        backend=backend,
    )

    assert result.status == "completed"
    assert result.generation_preview is None
    assert DatabasePendingReplacementStore(session).get_generation(conversation_id) is None


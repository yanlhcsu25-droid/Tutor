import json
from datetime import UTC, datetime

from calculus_agent.agent.identity import DEFAULT_TEACHER_OWNER_KEY
from calculus_agent.teaching_design.schemas import TeachingDesignContent
from calculus_agent.teaching_design.service import TeachingDesignService
from tests.evals.case_loader import EvalCase, load_eval_suite
from tests.evals.runner import _response_facts, collect_actual_state, run_case, write_report


def _tool_call(name, arguments):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": f"call-{name}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }],
    }


def test_response_facts_accepts_common_question_type_count_units():
    assert _response_facts(
        "选择题 4 题，填空题2道，计算题 3 道题，证明题1题。"
    )["question_type_counts"] == {
        "选择题": 4,
        "填空题": 2,
        "计算题": 3,
        "证明题": 1,
    }


def test_report_serializes_nested_datetime(tmp_path):
    report = tmp_path / "report.json"

    write_report([{
        "passed": True,
        "run_metadata": {"model_id": "test-model"},
        "provider": "siliconflow",
        "backend_adapter": "BailianChatBackend",
        "protocol": "openai_compatible",
        "actual": {"created_at": datetime(2026, 10, 3, tzinfo=UTC)},
    }], report)

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["results"][0]["actual"]["created_at"] == "2026-10-03 00:00:00+00:00"
    assert payload["metadata"]["providers"] == ["siliconflow"]
    assert payload["metadata"]["backend_adapters"] == ["BailianChatBackend"]
    assert payload["metadata"]["protocols"] == ["openai_compatible"]


def test_runner_projects_paper_db_and_response_facts():
    case = EvalCase(
        id="projection-read",
        category="paper",
        title="read count",
        setup={"current_paper": {"question_count": 10, "total_score": 100}},
        turns=[{"user": "当前这套卷总共有几道题？"}],
        expected={
            "status": "completed",
            "paper_db": {"persisted": True, "question_count": 10, "total_score": 100},
            "response_facts": {"question_count": 10},
        },
        backend={"behavior": "scripted", "responses": [
            _tool_call("read_paper", {}),
            {"role": "assistant", "content": "当前试卷共10题，总分100分。"},
        ]},
        graders=[
            {"type": "state"},
            {"type": "acceptance", "required_tools": ["read_paper"]},
        ],
    )

    result = run_case(case)

    assert result["passed"], result
    assert result["actual"]["paper_db"]["question_count"] == 10
    assert result["actual"]["response_facts"] == {
        "question_count": 10,
        "total_score": 100,
    }


def test_runner_projects_unified_pending_paper_change():
    case = EvalCase(
        id="projection-change",
        category="paper",
        title="replace preview",
        setup={"current_paper": {"question_count": 10, "total_score": 100}},
        turns=[{"user": "把第3道计算题换简单一点，先预览。"}],
        expected={
            "status": "waiting_confirmation",
            "pending_paper_change": {"exists": True},
        },
        backend={"behavior": "scripted", "responses": [
            _tool_call("read_paper", {}),
            _tool_call("preview_paper_changes", {
                "operations": [{
                    "type": "replace_question",
                    "target": {"section_type": "计算题", "section_order": 3},
                    "difficulty_direction": "easier",
                }],
            }),
            {"role": "assistant", "content": "已创建替换预览，请确认。"},
        ]},
        graders=[
            {"type": "state"},
            {"type": "acceptance", "required_tools": [
                "read_paper", "preview_paper_changes",
            ]},
        ],
    )

    result = run_case(case)

    assert result["passed"], result
    assert result["actual"]["pending_paper_change"]["exists"] is True


def test_consultation_negation_does_not_create_business_artifact():
    suite = load_eval_suite("tests/evals/cases/golden_smoke_v1.yaml")
    case = next(item for item in suite.cases if item.id == "SMOKE-CONSULT-01")
    case.backend = {"behavior": "scripted", "responses": [{
        "role": "assistant",
        "content": "高阶导数教学应强调递推结构、符号规律和典型例题。",
    }]}

    result = run_case(case)

    assert result["passed"], result
    assert result["actual"]["active_teaching_design"] is None
    assert result["actual"]["pending_generation"] is None
    assert result["actual"]["pending_paper_change"] is None


def test_explicit_type_distribution_is_restored_before_pending_persistence():
    suite = load_eval_suite("tests/evals/cases/golden_smoke_v1.yaml")

    for case_id in ("SMOKE-GEN-02", "SMOKE-GEN-03"):
        case = next(item for item in suite.cases if item.id == case_id)
        case.backend = {"behavior": "scripted", "responses": [
            _tool_call("prepare_generation_plan", {
                "scope_names": ["第一章 函数与极限"],
                "question_count": case.expected["pending_generation"]["question_count"],
                "total_score": case.expected["pending_generation"]["total_score"],
            }),
            {"role": "assistant", "content": "已完整保留教师指定的题型分布。"},
        ]}

        result = run_case(case)

        assert result["passed"], result


def test_golden_read_cases_require_current_version_read_after_analysis():
    suite = load_eval_suite("tests/evals/cases/golden_smoke_v1.yaml")
    responses = {
        "SMOKE-READ-02": "这套卷满分是100分。",
        "SMOKE-READ-03": "选择题 4 题，填空题 2 题，计算题 3 题，证明题 1 题。",
    }

    for case_id, final_message in responses.items():
        case = next(item for item in suite.cases if item.id == case_id)
        case.backend = {"behavior": "scripted", "responses": [
            _tool_call("analyze_paper", {}),
            {"role": "assistant", "content": final_message},
            _tool_call("read_paper", {}),
            {"role": "assistant", "content": final_message},
        ]}

        result = run_case(case)

        assert result["passed"], result
        assert [call["name"] for call in result["actual"]["trace"]["tool_calls"]] == [
            "analyze_paper", "read_paper",
        ]


def test_pending_patches_restore_explicit_field_and_inherit_the_rest():
    suite = load_eval_suite("tests/evals/cases/golden_smoke_v1.yaml")
    expected = {
        "SMOKE-PENDING-01": {"question_count": 7, "total_score": 100},
        "SMOKE-PENDING-03": {"question_count": 10, "total_score": 90},
    }

    for case_id, fields in expected.items():
        case = next(item for item in suite.cases if item.id == case_id)
        case.backend = {"behavior": "scripted", "responses": [
            _tool_call("prepare_generation_plan", {}),
            {"role": "assistant", "content": "方案已按要求更新，其他条件不变。"},
        ]}

        result = run_case(case)

        assert result["passed"], result
        pending = result["actual"]["pending_generation"]
        assert {key: pending[key] for key in fields} == fields


def test_compact_score_survives_preview_and_confirmation():
    suite = load_eval_suite("tests/evals/cases/golden_smoke_v1.yaml")
    case = next(item for item in suite.cases if item.id == "SMOKE-MULTI-02")
    case.backend = {"behavior": "scripted", "responses": [
        _tool_call("prepare_generation_plan", {
            "scope_names": ["第一章 函数与极限"],
            "question_count": 6,
            "total_score": 60,
        }),
        {"role": "assistant", "content": "已创建6题60分方案，请确认。"},
        _tool_call("confirm_generation", {}),
        {"role": "assistant", "content": "已生成6题，总分60分。"},
    ]}

    result = run_case(case)

    assert result["passed"], result
    assert result["actual"]["paper_db"]["total_score"] == 60


def test_ambiguous_address_golden_case_passes_without_model_call():
    suite = load_eval_suite("tests/evals/cases/golden_smoke_v1.yaml")
    case = next(item for item in suite.cases if item.id == "SMOKE-CLARIFY-01")
    case.backend = {"behavior": "scripted", "responses": []}

    result = run_case(case)

    assert result["passed"], result
    assert result["model_calls"] == 0
    assert result["tool_calls"] == 0
    assert result["actual"]["pending_paper_change"] is None


def test_runner_grades_tool_lifecycle_across_multiple_turns():
    suite = load_eval_suite("tests/evals/cases/golden_smoke_v1.yaml")
    case = next(item for item in suite.cases if item.id == "SMOKE-GEN-04")
    case.backend = {"behavior": "scripted", "responses": [
        _tool_call("prepare_generation_plan", {
            "scope_names": ["第一章 函数与极限"],
            "question_count": 6,
            "total_score": 60,
            "question_type_requirements": [{"question_type": "计算题", "count": 6}],
        }),
        {"role": "assistant", "content": "方案已保留全部约束，请确认。"},
        _tool_call("confirm_generation", {}),
        {"role": "assistant", "content": "计算题供给不足，不能放宽约束。"},
    ]}

    result = run_case(case)

    assert result["passed"], result
    assert [call["name"] for call in result["actual"]["trace"]["tool_calls"]] == [
        "prepare_generation_plan", "confirm_generation",
    ]


def test_runner_projects_active_teaching_design_and_lesson_count(session):
    TeachingDesignService(session).create(
        owner_key=DEFAULT_TEACHER_OWNER_KEY,
        conversation_id="projection-design",
        content=TeachingDesignContent(
            title="第一章复习",
            objective="巩固函数与极限",
            scope_names=["第一章 函数与极限"],
            lesson_count=2,
        ),
        run_id="run-design",
        source_user_message="安排两课时",
        ready_for_confirmation=True,
    )

    actual = collect_actual_state(
        session=session,
        conversation_id="projection-design",
        result=None,
    )

    assert actual["active_teaching_design"]["persisted"] is True
    assert actual["active_teaching_design"]["status"] == "awaiting_confirmation"
    assert actual["active_teaching_design"]["scope_names"] == ["第一章 函数与极限"]
    assert actual["active_teaching_design"]["lesson_count"] == 2

"""Offline checks for exact aliases vs. undecidable curriculum progression."""
import pytest

from evaluation.teaching_quality.runners.scope_review import assess_scope
from evaluation.teaching_quality.runners.inspect_saved_design import inspect


ALLOWED = {"id": "progress_scope", "type": "allowed_scope_only", "critical": True,
           "target": "teaching_design.topics", "expected": ["导数定义", "几何意义", "基础导数理解"]}
SETUP = {"course_progress": "刚讲完导数定义"}
FORBIDDEN = ["高阶导数", "中值定理", "泰勒公式"]


def test_safe_aliases_accepted():
    got = assess_scope(ALLOWED, ["导数的定义", "导数的几何意义"], SETUP, forbidden=FORBIDDEN)
    assert got["status"] == "PASS_LEXICAL"
    assert got["canonical"] == ["导数定义", "几何意义"]


@pytest.mark.parametrize("term", ["左导数与右导数", "可导性与连续性的关系", "高阶导数的应用"])
def test_semantically_dependent_topic_is_not_auto_passed(term):
    got = assess_scope(ALLOWED, ["导数的定义", term], SETUP, forbidden=FORBIDDEN)
    assert got["status"] == "NEEDS_SEMANTIC_REVIEW"
    assert got["unresolved_topics"] == [term]


def test_explicit_prohibited_topic_fails():
    got = assess_scope(ALLOWED, ["导数的定义", "高阶导数"], SETUP, forbidden=FORBIDDEN)
    assert got["status"] == "FAIL_EXPLICIT_FORBIDDEN"
    assert got["explicit_forbidden"] == ["高阶导数"]


def test_empty_observation_is_not_a_quality_pass():
    got = assess_scope(ALLOWED, [], SETUP)
    assert got["status"] == "MISSING_OBSERVATION"
    assert got["canonical"] == []


def test_invalid_observed_data_refused():
    with pytest.raises(ValueError):
        assess_scope(ALLOWED, "导数定义", SETUP)


def test_saved_td013_requires_review_without_model():
    report = {
        "kind": "diagnostic_real_agent_dev_capture", "blind_executed": False,
        "official_benchmark": False,
        "results": [{"case_id": "TQ-TD-013", "execution_status": "CAPTURED",
                     "agent_status": "waiting_confirmation", "actual_output": {
                         "teaching_design": {
                             "knowledge_plan": [{"name": "导数的定义"},
                                                {"name": "可导性与连续性的关系"},
                                                {"name": "左导数与右导数"}],
                             "teaching_sequence": [{"knowledge_points": ["导数的几何意义"]}],
                             "lecture_plan": {"structure": ["概念回顾（10分钟）",
                                                            "重点难点讲解（15分钟）",
                                                            "例题演练（15分钟）",
                                                            "小结与答疑（5分钟）"]}}}}]
    }
    case = {"case_id":"TQ-TD-013", "setup":SETUP,
            "expected":{"structural_assertions":[ALLOWED,
                {"id":"no_advanced", "type":"forbidden_content", "critical": True,
                 "target":"teaching_design", "forbidden":FORBIDDEN, "match_mode":"semantic"},
                {"id":"duration", "type":"total_duration_max", "critical":True,
                 "target":"teaching_design.minutes", "expected":45}]}}
    result = inspect(report, case)
    statuses = {r["assertion_id"]:r["status"] for r in result["checks"]}
    assert statuses == {"progress_scope": "NEEDS_SEMANTIC_REVIEW",
                        "no_advanced":"NEEDS_SEMANTIC_JUDGE", "duration":"PASS"}
    review = result["checks"][0]["review"]
    assert review["unresolved_topics"] == ["可导性与连续性的关系", "左导数与右导数"]
    assert result["model_calls"] == 0 and not result["blind_executed"]


def test_runner_marks_ambiguous_scope_unscorable_not_failed():
    from evaluation.teaching_quality.runners.run_dev_eval import grade_result

    class OfflineBackend:
        def complete(self, prompt):
            return {}

    actual = {"teaching_design": {"knowledge_plan": [{"name": "导数的定义"},
                                                     {"name": "左导数与右导数"}]}}
    case = {"setup": SETUP, "expected": {"structural_assertions": [ALLOWED],
                                       "behavioral_rubric": {"axes": {"scope_fidelity": 1.0}}}}
    result = grade_result(case, actual, OfflineBackend())
    assert result["status"] == "NOT_FULLY_SCORABLE"
    assert result["unscored_assertions"][0]["error"] == "SEMANTIC_SCOPE_REVIEW_REQUIRED"
    assert result["structural"] == []


def test_runner_grades_strict_synonyms_without_scope_failure():
    from evaluation.teaching_quality.runners.run_dev_eval import grade_result

    class OfflineBackend:
        def complete(self, prompt):
            return {}

    actual = {"teaching_design": {"knowledge_plan": [{"name": "导数的定义"},
                                                     {"name": "导数的几何意义"}]}}
    case = {"setup": SETUP, "expected": {"structural_assertions": [ALLOWED],
                                       "behavioral_rubric": {"axes": {"scope_fidelity": 1.0}}}}
    result = grade_result(case, actual, OfflineBackend())
    assert result["unscored_assertions"] == []
    assert result["structural"][0]["passed"] is True

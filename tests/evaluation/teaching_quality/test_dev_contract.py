from copy import deepcopy

from evaluation.teaching_quality.runners.dev_contract import adapt_dev_observation


def test_explicit_lessons_count_not_fabricated():
    actual = {"teaching_design": {"lesson_count": 2}}
    original = deepcopy(actual)
    assertion = {"type": "exact_count", "target": "teaching_design.lessons"}
    adapted, observed, notes = adapt_dev_observation(assertion, actual)
    assert adapted["target"] == "teaching_design.lesson_count"
    assert "lessons" not in observed["teaching_design"]
    assert original == actual
    assert notes


def test_scope_names_are_not_topic_guesses():
    a = {"target": "teaching_design.chapters", "type": "allowed_scope_only"}
    actual = {"teaching_design": {"scope_names": ["第一章 函数与极限", "第三章 微分中值定理"]}}
    _, observed, notes = adapt_dev_observation(a, actual)
    assert observed["teaching_design"]["observed_scope_chapters"] == ["第一章", "第三章"]
    assert notes
    bad = {"teaching_design": {"scope_names": ["第一章 函数与极限", "无法核实的范围"]}}
    _, observed, _ = adapt_dev_observation(a, bad)
    assert "observed_scope_chapters" not in observed["teaching_design"]


def test_explicit_duration_not_setup_duration():
    a = {"target": "response.plan.minutes", "type": "total_duration_max"}
    out = {"response": "先讲例题（约15分钟），再完成练习（约20分钟），最后总结（约10分钟）。"}
    adapted, observed, _ = adapt_dev_observation(a, out)
    assert adapted["target"] == "response_plan_minutes_observed"
    assert observed["response_plan_minutes_observed"] == 45
    b, observed2, _ = adapt_dev_observation(a, {"response": "有45分钟可以学习"})
    assert b["target"] == "response.plan.minutes"
    assert "response_plan_minutes_observed" not in observed2
    b, o, _ = adapt_dev_observation(a, {"response": "先（约10分钟），再（约10分钟），另安排15分钟复习"})
    assert b["target"] == "response.plan.minutes"
    assert "response_plan_minutes_observed" not in o


def test_no_fabricated_examples_or_lessons():
    x = {"teaching_design": {"teaching_sequence": [{"knowledge_points": ["极限"]}], "example_strategy": "每节课讲例题"}}
    a = {"target": "teaching_design.topics", "type": "coverage_contains"}
    _, y, _ = adapt_dev_observation(a, x)
    assert y["teaching_design"]["topics"] == ["极限"]
    assert "examples" not in y["teaching_design"]
    assert "lessons" not in y["teaching_design"]


def test_topics_can_project_explicit_lexical_equivalence_but_not_invent_examples():
    a = {"target": "teaching_design.topics", "type": "coverage_contains", "expected": ["函数极限", "间断点"]}
    o = {"teaching_design": {"knowledge_plan": [{"name": "函数的极限"}, {"name": "可去间断点"}]}}
    adapted, observed, notes = adapt_dev_observation(a, o)
    assert "函数极限" in observed["teaching_design"]["topics"]
    assert "间断点" in observed["teaching_design"]["topics"]
    assert "examples" not in observed["teaching_design"]
    assert notes


def test_offline_audit_refuses_blind_or_official_report():
    import pytest

    from evaluation.teaching_quality.runners.audit_saved_dev import audit_report

    with pytest.raises(ValueError, match="Only diagnostic Dev"):
        audit_report({"kind": "diagnostic_real_agent_dev_capture", "blind_executed": True,
                      "official_benchmark": False}, [])

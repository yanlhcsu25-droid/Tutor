"""Regression tests for paper-question-operations Skill."""

from pathlib import Path

from calculus_agent.agent.skills import load_skill, load_skill_bundle


def test_question_operation_skill_loads():
    text = load_skill("paper_question_operations")

    assert "name: paper_question_operations" in text
    assert "# 当前试卷题目操作" in text
    assert "preview_adjust_paper" in text
    assert "remove_addresses" in text
    assert "Tool Observation" in text


def test_question_operation_skill_bundle_has_boundaries():
    text = load_skill_bundle("paper_question_operations")

    assert text.startswith(
        '<active_skill name="paper_question_operations">'
    )
    assert text.rstrip().endswith("</active_skill>")


def test_agent_has_question_operation_skill_integration():
    import calculus_agent.agent.agent as agent_module

    source = Path(agent_module.__file__).read_text(encoding="utf-8")

    assert "from .skills import load_skill_bundle" in source
    assert 'QUESTION_OPERATION_SKILL = "paper_question_operations"' in source
    assert "load_skill_bundle(" in source
    assert "QUESTION_OPERATION_SKILL" in source
    assert "question_operation_skill_active" in source

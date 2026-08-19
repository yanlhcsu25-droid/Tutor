"""Regression test: the skill must be present before the model decides."""

from calculus_agent.agent.skills import load_skill_bundle


def test_paper_question_operation_skill_loads():
    text = load_skill_bundle("paper_question_operations")
    assert '<active_skill name="paper_question_operations">' in text
    assert "preview_paper_changes" in text
    assert "confirm_paper_changes" in text
    assert "discard_pending_plan" in text
    assert "Tool Observation" in text

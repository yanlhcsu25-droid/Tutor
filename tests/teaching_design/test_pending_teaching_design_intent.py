from calculus_agent.agent.pending_teaching_design_intent import (
    classify_pending_teaching_design_intent,
)


def test_pending_design_intents_are_structured_and_deterministic():
    assert classify_pending_teaching_design_intent("可以，就按这个").action == "confirm"
    assert classify_pending_teaching_design_intent("基础一点").action == "revise"
    assert classify_pending_teaching_design_intent("重点放两个重要极限").action == "revise"
    assert classify_pending_teaching_design_intent("这个方案里为什么安排证明题？").action == "query"
    assert classify_pending_teaching_design_intent("算了不要这个方案").action == "cancel"


def test_revision_marker_wins_over_confirmation_word():
    result = classify_pending_teaching_design_intent("可以，但基础一点")
    assert result.action == "revise"
    assert result.revision_request == "可以，但基础一点"

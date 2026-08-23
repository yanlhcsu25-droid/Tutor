from calculus_agent.papers.addressing import QuestionAddress
from calculus_agent.runtime.grounding_policy import GroundingPolicy


def test_current_question_requires_read_without_llm_decision():
    decision = GroundingPolicy.evaluate(
        message="第2题是什么？",
        addresses=[], positions=[], current_version_id="v1", observed_read_versions=set(),
    )
    assert decision.requires_current_paper_evidence
    assert decision.read_required


def test_same_version_successful_read_is_fresh():
    decision = GroundingPolicy.evaluate(
        message="这张卷子多少分？",
        addresses=[], positions=[], current_version_id="v1", observed_read_versions={"v1"},
    )
    assert decision.has_fresh_observation
    assert not decision.read_required


def test_old_version_read_is_not_fresh():
    decision = GroundingPolicy.evaluate(
        message="计算题第3题是什么？",
        addresses=[QuestionAddress(section_type="计算题", section_order=3)],
        positions=[], current_version_id="v2", observed_read_versions={"v1"},
    )
    assert decision.requires_current_paper_evidence
    assert not decision.has_fresh_observation
    assert decision.read_required


def test_greeting_does_not_require_current_paper_evidence():
    decision = GroundingPolicy.evaluate(
        message="你好", addresses=[], positions=[], current_version_id="v1", observed_read_versions=set(),
    )
    assert not decision.requires_current_paper_evidence
    assert not decision.read_required

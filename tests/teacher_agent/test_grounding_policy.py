import pytest

from calculus_agent.papers.addressing import QuestionAddress
from calculus_agent.runtime.grounding_policy import GroundingPolicy


def evaluate(
    message: str,
    *,
    version: str | None = "v1",
    addresses: list[QuestionAddress] | None = None,
    positions: list[int] | None = None,
    evidence: dict[str, set[str]] | None = None,
):
    return GroundingPolicy.evaluate(
        message=message,
        addresses=addresses or [],
        positions=positions or [],
        current_version_id=version,
        observed_paper_evidence=evidence or {},
    )


def test_analysis_requires_every_summary_field():
    decision = evaluate(
        "分析当前试卷统计",
        evidence={
            "v1": {
                "score_total",
                "question_count",
                "question_type_distribution",
                "difficulty_distribution",
            },
        },
    )
    assert decision.required_evidence == {
        "score_total",
        "question_count",
        "question_type_distribution",
        "difficulty_distribution",
        "knowledge_distribution",
    }
    assert not decision.has_fresh_observation
    assert decision.read_required


def test_multiple_summary_fields_are_all_required():
    decision = evaluate("当前试卷总题数和总分是多少？")
    assert decision.required_evidence == {"score_total", "question_count"}
    assert decision.read_required


def test_difficulty_and_knowledge_are_both_required():
    decision = evaluate("当前卷难度分布和知识点分布是什么？")
    assert decision.required_evidence == {
        "difficulty_distribution",
        "knowledge_distribution",
    }


def test_address_requires_matching_question_detail():
    address = QuestionAddress(section_type="计算题", section_order=3)
    decision = evaluate(
        "计算题第3题是什么？",
        addresses=[address],
        evidence={"v1": {"question_detail:address:计算题:3"}},
    )
    assert decision.required_evidence == {"question_detail:address:计算题:3"}
    assert decision.has_fresh_observation
    assert not decision.read_required


def test_multiple_addresses_are_all_required():
    addresses = [
        QuestionAddress(section_type="选择题", section_order=1),
        QuestionAddress(section_type="计算题", section_order=2),
    ]
    decision = evaluate("读取两道题", addresses=addresses)
    assert decision.required_evidence == {
        "question_detail:address:选择题:1",
        "question_detail:address:计算题:2",
    }


def test_position_requires_matching_question_detail():
    decision = evaluate(
        "读取全卷第3题",
        positions=[3],
        evidence={"v1": {"question_detail:position:3"}},
    )
    assert decision.required_evidence == {"question_detail:position:3"}
    assert decision.has_fresh_observation


def test_addresses_take_precedence_over_positions():
    address = QuestionAddress(section_type="填空题", section_order=2)
    decision = evaluate("读取指定题目", addresses=[address], positions=[5])
    assert decision.required_evidence == {"question_detail:address:填空题:2"}


def test_old_version_evidence_is_not_fresh():
    address = QuestionAddress(section_type="计算题", section_order=3)
    decision = evaluate(
        "计算题第3题是什么？",
        version="v2",
        addresses=[address],
        evidence={"v1": {"question_detail:address:计算题:3"}},
    )
    assert decision.requires_current_paper_evidence
    assert not decision.has_fresh_observation
    assert decision.read_required


def test_unparsed_single_question_cannot_use_whole_paper_overview():
    decision = evaluate(
        "第3题是什么？",
        evidence={"v1": {"question_overview"}},
    )
    assert decision.required_evidence == {"question_detail:unresolved_reference"}
    assert decision.read_required


def test_general_paper_query_requires_question_overview():
    decision = evaluate("这套卷有哪些题目？")
    assert decision.required_evidence == {"question_overview"}


def test_question_overview_for_current_version_is_fresh():
    decision = evaluate(
        "这套卷有哪些题目？",
        evidence={"v1": {"question_overview"}},
    )
    assert decision.has_fresh_observation
    assert not decision.read_required


def test_missing_current_version_cannot_use_evidence():
    decision = evaluate(
        "当前试卷总分是多少？",
        version=None,
        evidence={"v1": {"score_total"}},
    )
    assert not decision.has_fresh_observation
    assert decision.read_required


def test_greeting_does_not_require_current_paper_evidence():
    decision = evaluate("你好")
    assert not decision.requires_current_paper_evidence
    assert decision.required_evidence == set()
    assert not decision.read_required


@pytest.mark.parametrize(
    "message",
    [
        "这套卷满分多少？不要修改。",
        "当前卷各种题型分别有多少题？",
    ],
)
def test_paper_summary_queries_require_current_version_evidence(message):
    decision = evaluate(message)
    assert decision.requires_current_paper_evidence
    assert decision.read_required

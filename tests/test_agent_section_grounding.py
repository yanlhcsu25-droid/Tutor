from calculus_agent.agent.agent import (
    _explicit_question_addresses,
    _explicit_question_positions,
    _paper_read_messages,
)
from calculus_agent.papers.addressing import QuestionAddress


def test_extract_section_local_question_address():
    assert _explicit_question_addresses(
        "请告诉我填空题第2题是什么"
    ) == [
        QuestionAddress(
            section_type="填空题",
            section_order=2,
        )
    ]


def test_reversed_natural_section_address_is_supported():
    assert _explicit_question_addresses(
        "第三题这道填空题是什么"
    ) == [
        QuestionAddress(
            section_type="填空题",
            section_order=3,
        )
    ]


def test_section_local_number_is_not_legacy_global_position():
    assert _explicit_question_positions(
        "请告诉我填空题第2题是什么"
    ) == []


def test_explicit_whole_paper_position_remains_supported():
    assert _explicit_question_positions(
        "请告诉我全卷第2题是什么"
    ) == [2]


def test_bare_question_number_is_not_forced_to_global_position():
    assert _explicit_question_addresses(
        "请告诉我第2题是什么"
    ) == []

    assert _explicit_question_positions(
        "请告诉我第2题是什么"
    ) == []


def test_paper_read_prompt_uses_section_address():
    messages = _paper_read_messages(
        message="填空题第2题是什么",
        serialized_context="{}",
        requested_addresses=[
            QuestionAddress(
                section_type="填空题",
                section_order=2,
            )
        ],
    )

    system = messages[0]["content"]

    assert "read_current_paper(addresses=" in system
    assert "不得转换成全卷 position" in system

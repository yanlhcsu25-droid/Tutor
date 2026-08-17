import pytest
from pydantic import ValidationError

from calculus_agent.agent.tool_registry import PreviewReplacementInput
from calculus_agent.papers.addressing import QuestionAddress


def test_preview_replacement_accepts_section_address():
    request = PreviewReplacementInput(
        address=QuestionAddress(section_type="填空题", section_order=2),
        difficulty_direction="easier",
    )
    assert request.address is not None
    assert request.address.section_type == "填空题"
    assert request.address.section_order == 2
    assert request.position is None


def test_preview_replacement_keeps_legacy_internal_position():
    request = PreviewReplacementInput(position=4, difficulty_direction="harder")
    assert request.position == 4
    assert request.address is None


def test_preview_replacement_rejects_mixed_address_modes():
    with pytest.raises(ValidationError):
        PreviewReplacementInput(
            address=QuestionAddress(section_type="填空题", section_order=2),
            position=4,
            difficulty_direction="easier",
        )


def test_preview_replacement_requires_one_target():
    with pytest.raises(ValidationError):
        PreviewReplacementInput(difficulty_direction="easier")


def test_preview_replacement_schema_marks_position_as_legacy():
    schema = PreviewReplacementInput.model_json_schema()
    assert "address" in schema["properties"]
    assert "position" in schema["properties"]
    assert "Legacy internal global" in schema["properties"]["position"]["description"]

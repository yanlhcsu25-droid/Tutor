import pytest
from pydantic import ValidationError

from calculus_agent.agent.paper_change_service import (
    PaperChangeRequest,
    ReplaceQuestionChange,
)


def test_replace_question_accepts_section_address():
    request = PaperChangeRequest(
        operations=[
            {
                "type": "replace_question",
                "target": {
                    "section_type": "填空题",
                    "section_order": 2,
                },
                "difficulty_direction": "easier",
            }
        ]
    )

    operation = request.operations[0]

    assert isinstance(operation, ReplaceQuestionChange)
    assert operation.target.section_type == "填空题"
    assert operation.target.section_order == 2
    assert operation.difficulty_direction == "easier"


def test_replace_question_rejects_legacy_internal_position():
    with pytest.raises(ValidationError):
        PaperChangeRequest(
            operations=[
                {
                    "type": "replace_question",
                    "position": 4,
                    "difficulty_direction": "harder",
                }
            ]
        )


def test_replace_question_rejects_mixed_address_modes():
    with pytest.raises(ValidationError):
        PaperChangeRequest(
            operations=[
                {
                    "type": "replace_question",
                    "target": {
                        "section_type": "填空题",
                        "section_order": 2,
                    },
                    "position": 4,
                    "difficulty_direction": "easier",
                }
            ]
        )


def test_replace_question_requires_target():
    with pytest.raises(ValidationError):
        PaperChangeRequest(
            operations=[
                {
                    "type": "replace_question",
                    "difficulty_direction": "easier",
                }
            ]
        )


def test_replace_question_schema_exposes_only_teacher_facing_target():
    schema = ReplaceQuestionChange.model_json_schema()
    properties = schema["properties"]

    assert "target" in properties
    assert "position" not in properties
    assert "difficulty_direction" in properties
    assert "preserve_knowledge_points" in properties

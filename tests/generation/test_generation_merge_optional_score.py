from __future__ import annotations

from calculus_agent.agent.schemas import (
    GeneratePaperInput,
    GenerationPlanPatch,
    QuestionTypePatch,
    QuestionTypeRequirement,
)
from calculus_agent.agent.services.generation import (
    _merge_question_type_patch,
)


def _by_type(request: GeneratePaperInput) -> dict[str, QuestionTypeRequirement]:
    return {
        item.question_type: item
        for item in (request.question_type_requirements or [])
    }


def test_count_only_patch_with_unknown_score_does_not_crash() -> None:
    base = GeneratePaperInput(
        paper_type="chapter_test",
        question_type_requirements=[
            QuestionTypeRequirement(
                question_type="计算题",
                count=4,
                score_each=None,
                total_score=None,
            ),
        ],
    )

    patch = GenerationPlanPatch(
        question_type_patches=[
            QuestionTypePatch(
                question_type="计算题",
                count=5,
            ),
        ],
    )

    merged, changed_counts, changed_scores = _merge_question_type_patch(
        base,
        patch.model_dump(exclude_unset=True),
    )

    item = _by_type(merged)["计算题"]

    assert item.count == 5
    assert item.score_each is None
    assert item.total_score is None
    assert merged.question_count == 5
    assert changed_counts == {"计算题"}
    assert changed_scores == set()


def test_count_only_patch_recomputes_total_when_score_is_known() -> None:
    base = GeneratePaperInput(
        paper_type="chapter_test",
        question_type_requirements=[
            QuestionTypeRequirement(
                question_type="计算题",
                count=4,
                score_each=10,
                total_score=40,
            ),
        ],
    )

    patch = GenerationPlanPatch(
        question_type_patches=[
            QuestionTypePatch(
                question_type="计算题",
                count=5,
            ),
        ],
    )

    merged, changed_counts, changed_scores = _merge_question_type_patch(
        base,
        patch.model_dump(exclude_unset=True),
    )

    item = _by_type(merged)["计算题"]

    assert item.count == 5
    assert item.score_each == 10
    assert item.total_score == 50
    assert merged.question_count == 5
    assert changed_counts == {"计算题"}
    assert changed_scores == set()

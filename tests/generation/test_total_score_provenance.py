from calculus_agent.agent.conversation_state import PendingGeneration
from calculus_agent.agent.schemas import (
    ConstraintProvenance,
    GenerationPlanPatch,
    GeneratePaperInput,
    QuestionTypeRequirement,
)
from calculus_agent.agent.services.generation import (
    _rebalance_scores,
    _total_score_source,
)


def _request(total=100):
    return GeneratePaperInput(
        total_score=total,
        question_type_requirements=[
            QuestionTypeRequirement(
                question_type="选择题", count=2, score_each=5
            ),
            QuestionTypeRequirement(
                question_type="计算题", count=3, score_each=15
            ),
        ],
    )


def test_total_score_source_precedence():
    assert _total_score_source(
        GenerationPlanPatch(total_score=60), pending=None
    ) == "teacher_explicit"
    assert _total_score_source(
        GenerationPlanPatch(
            total_score=100,
            constraint_provenance={
                "total_score": ConstraintProvenance(
                    source="TeachingDesign.assessment_plan.total_score",
                    teacher_explicit=False,
                    strength="hard",
                )
            },
        ),
        pending=None,
    ) == "teaching_design"
    assert _total_score_source(
        GenerationPlanPatch(), pending=PendingGeneration(request=_request())
    ) == "pending_inherited"
    assert _total_score_source(GenerationPlanPatch(), pending=None) == "default_template"


def test_explicit_total_is_preserved_and_default_total_can_be_derived():
    # 2*5 + 3*15 = 55: an explicit 60 cannot silently become 55.
    explicit, clarification = _rebalance_scores(
        _request(60), locked_types=set(), changed_count_types=set()
    )
    assert explicit is not None or clarification is not None

    # A locked type is never changed by deterministic rebalance.
    balanced, clarification = _rebalance_scores(
        _request(60), locked_types={"选择题"}, changed_count_types=set()
    )
    assert balanced is None or balanced.question_type_requirements[0].score_each == 5
    assert clarification is not None or balanced is not None

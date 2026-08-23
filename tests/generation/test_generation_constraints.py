from calculus_agent.agent.schemas import GeneratePaperInput
from calculus_agent.generation.constraints import GenerationConstraints


def test_generation_constraints_constructs_canonical_fields():
    constraints = GenerationConstraints(
        chapter_scope=["第一章"],
        knowledge_points=["极限"],
        question_count=10,
        difficulty_range=["easy", "normal"],
        question_type_distribution={"选择题": 5, "计算题": 5},
        total_score=100,
    )
    assert constraints.chapter_scope == ["第一章"]
    assert constraints.total_score == 100
    assert constraints.excluded_question_ids == set()


def test_constraints_translate_existing_structured_intent_without_schema_change():
    intent = GeneratePaperInput(
        scope_names=["第一章"],
        required_knowledge_names=["极限"],
        question_count=10,
        total_score=100,
        difficulty_level="normal",
    )
    constraints = GenerationConstraints.from_generation_input(intent)
    assert constraints.chapter_scope == ["第一章"]
    assert constraints.knowledge_points == ["极限"]
    assert constraints.question_count == 10
    assert constraints.total_score == 100


def test_exclusions_are_prepared_for_future_candidate_filtering():
    constraints = GenerationConstraints(excluded_question_ids={"q2", "q3"})
    assert constraints.merged_exclusions(["q1", "q2"]) == ["q1", "q2", "q3"]


def test_runtime_contains_no_generation_selection_implementation():
    source = open("src/calculus_agent/runtime/coordinator.py").read()
    assert "compose_paper_with_evidence" not in source
    assert "cp_model" not in source

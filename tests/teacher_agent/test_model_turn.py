from calculus_agent.runtime.model_turn import prepare_model_turn


class _ContextBuilder:
    def project_workspace(self, context):
        return context


def test_model_turn_preparation_keeps_skill_order_and_span_data():
    prepared = prepare_model_turn(
        messages=[{"role": "user", "content": "你好"}],
        definitions=[],
        serialized_context="{}",
        recent_messages=[],
        context_builder=_ContextBuilder(),
        dynamic_context={},
        teaching_design_skill_active=True,
        question_operation_skill_active=True,
        teaching_design_skill_name="teaching_design",
        question_operation_skill_name="paper_question_operations",
        tool_round=2,
    )
    assert prepared.active_skills == ("teaching_design", "paper_question_operations")
    assert prepared.span_input["tool_round"] == 2
    assert prepared.span_input["n_messages"] == 1
    assert prepared.span_input["context_metrics"] == prepared.context_metrics.as_dict()

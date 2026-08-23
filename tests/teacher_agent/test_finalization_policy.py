from calculus_agent.runtime.finalization_policy import FinalizationInput, normalize_finalization


class _RuntimePolicy:
    def preserves_pending_state(self, _code):
        return False

    def is_clarification_error(self, _code):
        return False


def _data(**changes):
    values = dict(
        status="completed", final_text="已创建教学设计。",
        result_values={"blocking_errors": [], "warnings": []}, turn_error=None,
        current_stage="response_parse", trace_calls=[], pending_query_possible=False,
        pending_action_in_store=False, teaching_design_artifact_requested=False,
        active_design=None, active_task_status=None,
    )
    values.update(changes)
    return FinalizationInput(**values)


def test_teaching_design_cannot_be_a_prose_only_success():
    data = _data(teaching_design_artifact_requested=True)
    result = normalize_finalization(data=data, runtime_policy=_RuntimePolicy())
    assert result.status == "failed"
    assert "teaching_design_not_created" in data.result_values["blocking_errors"]
    assert "不能声明" in result.final_text


def test_persisted_pending_state_normalizes_to_waiting_confirmation():
    result = normalize_finalization(
        data=_data(pending_query_possible=True, pending_action_in_store=True),
        runtime_policy=_RuntimePolicy(),
    )
    assert result.status == "waiting_confirmation"


def test_scope_selected_workflow_cannot_complete_without_artifact():
    data = _data(active_task_status="scope_selected")

    result = normalize_finalization(data=data, runtime_policy=_RuntimePolicy())

    assert result.status == "failed"
    assert "teaching_design_workflow_incomplete" in data.result_values["blocking_errors"]


def test_cross_paper_dedup_warning_replaces_unsafe_final_claim():
    data = _data(result_values={"blocking_errors": [], "warnings": ["avoid_previous_paper_questions_unsupported"]})
    result = normalize_finalization(data=data, runtime_policy=_RuntimePolicy())
    assert "不能保证题目不重复" in result.final_text

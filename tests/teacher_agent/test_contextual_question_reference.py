import json

import pytest
from sqlalchemy import select

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.agent.schemas import LastFocusedQuestion
from calculus_agent.models import AdjustmentPlanRecord, PaperItem
from calculus_agent.papers.addressing import QuestionAddress
from calculus_agent.runtime.paper_request import (
    _apply_question_reference_hints,
    has_ambiguous_question_mutation,
)
from calculus_agent.runtime.question_focus import (
    focus_from_explicit_reference,
    focus_from_pending_change,
    validated_focus_address,
)
from tests.evals.curriculum_fixture import seed_eval_curriculum
from tests.evals.case_loader import load_eval_suite
from tests.evals.fixtures.paper import seed_current_paper
from tests.evals.runner import run_case


class SequenceBackend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        return self.responses.pop(0)


class RouterClarifyingBackend(SequenceBackend):
    def __init__(self, *responses):
        super().__init__(*responses)
        self.structured_calls = 0

    def complete_structured(self, messages, schema):
        self.structured_calls += 1
        return {"message": {"content": json.dumps({
            "task_type": "INFORMATION_REQUEST",
            "confidence": 0.7,
            "artifact_required": False,
            "clarification_needed": True,
            "clarification_question": (
                "Which specific question do you want to replace, and what should "
                "the new question be or what topic should it cover?"
            ),
            "reason": "target or replacement specification appears omitted",
        })}}


def _tool(name, arguments):
    return {"message": {"role": "assistant", "content": None, "tool_calls": [{
        "id": f"call-{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }]}}


def _final(message):
    return {"message": {"role": "assistant", "content": message}}


def _paper(session):
    seed_eval_curriculum(session)
    return seed_current_paper(session, {"question_count": 10, "total_score": 100})


def test_frozen_ambiguous_target_cases_pass_without_calling_backend():
    suite = load_eval_suite(
        "tests/evals/staging/dev_v3_2_proposal/dev_122_revised_candidate.yaml"
    )
    for case_id in ("EVAL-152", "EVAL-192", "EVAL-194"):
        case = next(case for case in suite.cases if case.id == case_id)
        case.backend = {"behavior": "scripted", "responses": [{
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "wrong-guess",
                "type": "function",
                "function": {
                    "name": "preview_paper_changes",
                    "arguments": json.dumps({"operations": [{
                        "type": "remove_question",
                        "target": {"section_type": "选择题", "section_order": 1},
                    }]}, ensure_ascii=False),
                },
            }],
        }]}

        result = run_case(case)

        assert result["passed"], result
        assert result["actual"]["pending_paper_change"] is None
        assert result["actual"]["backend_calls"] == 0


@pytest.mark.parametrize("message", ["把这道题换掉。", "换一下刚才那题。"])
def test_contextual_mutation_without_focus_requires_clarification(session, message):
    context = _paper(session)
    backend = SequenceBackend()

    result = run_teacher_agent(
        session,
        message,
        conversation_id=f"no-focus-{message}",
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )

    assert result.status == "needs_clarification"
    assert "ambiguous_question_address" in result.blocking_errors
    assert DatabasePendingReplacementStore(session).get_adjustment(
        f"no-focus-{message}"
    ) is None
    assert backend.calls == 0


def test_contextual_read_without_focus_also_requires_clarification(session):
    context = _paper(session)
    backend = SequenceBackend()

    result = run_teacher_agent(
        session,
        "这道题是什么？",
        conversation_id="no-focus-read",
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )

    assert result.status == "needs_clarification"
    assert backend.calls == 0


def test_type_only_removal_never_defaults_to_first_candidate(session):
    context = _paper(session)
    backend = SequenceBackend()

    result = run_teacher_agent(
        session,
        "删除一道计算题。",
        conversation_id="ambiguous-computation",
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )

    assert result.status == "needs_clarification"
    assert DatabasePendingReplacementStore(session).get_adjustment(
        "ambiguous-computation"
    ) is None
    assert backend.calls == 0


@pytest.mark.parametrize("reference", ["把这道题换掉。", "换一下刚才那题。"])
def test_explicit_read_focus_overrides_model_guess_for_later_preview(session, reference):
    context = _paper(session)
    conversation_id = f"trusted-question-focus-{reference}"
    first = run_teacher_agent(
        session,
        "把第5题题干读给我。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=SequenceBackend(
            _tool("read_paper", {"positions": [5]}),
            _final("已读取当前卷第5题。"),
        ),
    )
    store = DatabasePendingReplacementStore(session)
    first_focus = store.get_memory(conversation_id).last_focused_question
    second = run_teacher_agent(
        session,
        reference,
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=SequenceBackend(
            _tool("read_paper", {
                "addresses": [{"section_type": "选择题", "section_order": 1}],
            }),
            _tool("preview_paper_changes", {"operations": [{
                "type": "replace_question",
                "target": {"section_type": "选择题", "section_order": 1},
                "difficulty_direction": "same",
            }]}),
            _final("已创建刚才那道题的替换预览，请确认。"),
        ),
    )

    focus = store.get_memory(conversation_id).last_focused_question
    plan = session.get(AdjustmentPlanRecord, store.get_adjustment(conversation_id))
    assert first.status == "completed"
    assert first_focus.position == 5
    assert first_focus.source == "read_paper"
    assert second.status == "waiting_confirmation"
    assert focus.position == 5
    assert focus.source == "pending_change"
    assert plan.operations_json[0]["position"] == 5


def test_valid_focus_routes_contextual_replacement_without_llm_router_clarification(
    session,
):
    context = _paper(session)
    conversation_id = "t04-router-boundary"
    backend = RouterClarifyingBackend(
        _tool("read_paper", {"positions": [7]}),
        _final("已读取全卷第7题。"),
        _tool("preview_paper_changes", {"operations": [{
            "type": "replace_question",
            "target": {"section_type": "选择题", "section_order": 1},
        }]}),
    )

    first = run_teacher_agent(
        session,
        "查看全卷第7题。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )
    second = run_teacher_agent(
        session,
        "把这道题换掉。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )

    store = DatabasePendingReplacementStore(session)
    plan = session.get(AdjustmentPlanRecord, store.get_adjustment(conversation_id))
    assert first.status == "completed"
    assert second.status == "waiting_confirmation"
    assert backend.structured_calls == 0
    assert backend.calls == 3
    assert plan.operations_json[0]["position"] == 7
    assert plan.operations_json[0]["_confirmation_contract"][
        "difficulty_direction"
    ] == "same"


def test_model_target_clarification_is_retried_when_focus_is_valid(session):
    context = _paper(session)
    conversation_id = "t04-model-clarification"
    first = run_teacher_agent(
        session,
        "查看全卷第7题。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=SequenceBackend(
            _tool("read_paper", {"positions": [7]}),
            _final("已读取全卷第7题。"),
        ),
    )
    backend = SequenceBackend(
        _final(
            "Which specific question do you want to replace, and what should "
            "the new question be or what topic should it cover?"
        ),
        _tool("preview_paper_changes", {"operations": [{
            "type": "replace_question",
            "target": {"section_type": "选择题", "section_order": 1},
        }]}),
    )

    second = run_teacher_agent(
        session,
        "把这道题换掉。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )

    store = DatabasePendingReplacementStore(session)
    plan = session.get(AdjustmentPlanRecord, store.get_adjustment(conversation_id))
    assert first.status == "completed"
    assert second.status == "waiting_confirmation"
    assert backend.calls == 2
    assert plan.operations_json[0]["position"] == 7


def test_stale_focus_version_keeps_contextual_replacement_ambiguous(session):
    context = _paper(session)
    conversation_id = "stale-focus-version"
    store = DatabasePendingReplacementStore(session)
    run_teacher_agent(
        session,
        "查看全卷第7题。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=SequenceBackend(
            _tool("read_paper", {"positions": [7]}),
            _final("已读取全卷第7题。"),
        ),
    )
    memory = store.get_memory(conversation_id)
    memory.last_focused_question.paper_version_id = "stale-version"
    store.set_memory(conversation_id, memory)
    backend = SequenceBackend()

    result = run_teacher_agent(
        session,
        "把这道题换掉。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )

    assert result.status == "needs_clarification"
    assert "ambiguous_question_address" in result.blocking_errors
    assert store.get_adjustment(conversation_id) is None
    assert backend.calls == 0


def test_incidental_random_read_does_not_become_focus(session):
    context = _paper(session)
    conversation_id = "incidental-random-read"

    result = run_teacher_agent(
        session,
        "临时随便读一道题给我。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=SequenceBackend(
            _tool("read_paper", {"positions": [1]}),
            _final("临时展示了一道题。"),
        ),
    )

    assert result.status == "completed"
    assert DatabasePendingReplacementStore(session).get_memory(
        conversation_id
    ).last_focused_question is None


def test_authoritative_focus_is_shared_by_read_remove_and_score_change():
    address = {"section_type": "计算题", "section_order": 2}
    focus = [QuestionAddress.model_validate(address)]

    read = _apply_question_reference_hints(
        tool_name="read_paper",
        arguments={"addresses": [{"section_type": "选择题", "section_order": 1}]},
        addresses=focus,
        positions=[],
    )
    remove = _apply_question_reference_hints(
        tool_name="preview_paper_changes",
        arguments={"operations": [{
            "type": "remove_question",
            "target": {"section_type": "选择题", "section_order": 1},
        }]},
        addresses=focus,
        positions=[],
    )
    score = _apply_question_reference_hints(
        tool_name="preview_paper_changes",
        arguments={"operations": [{
            "type": "change_question_score",
            "target": {"section_type": "选择题", "section_order": 1},
            "score": 12,
        }]},
        addresses=focus,
        positions=[],
    )

    assert read["addresses"] == [address]
    assert remove["operations"][0]["target"] == address
    assert score["operations"][0]["target"] == address


def test_focus_rejects_cross_paper_version_and_deleted_question(session):
    first = _paper(session)
    second = seed_current_paper(session, {"question_count": 10, "total_score": 100})
    focus = focus_from_explicit_reference(
        session,
        paper_id=first.paper_id,
        version_id=first.version_id,
        addresses=[],
        positions=[5],
    )

    assert validated_focus_address(
        session, focus=focus, paper_id=second.paper_id, version_id=second.version_id,
    ) is None
    assert validated_focus_address(
        session, focus=focus, paper_id=first.paper_id, version_id=second.version_id,
    ) is None

    item = session.scalar(select(PaperItem).where(
        PaperItem.paper_id == first.version_id,
        PaperItem.position == 5,
    ))
    session.delete(item)
    session.flush()
    assert validated_focus_address(
        session, focus=focus, paper_id=first.paper_id, version_id=first.version_id,
    ) is None


def test_pending_focus_requires_one_still_current_target(session):
    context = _paper(session)
    items = list(session.scalars(
        select(PaperItem)
        .where(PaperItem.paper_id == context.version_id)
        .order_by(PaperItem.position)
    ))
    single = AdjustmentPlanRecord(
        paper_id=context.paper_id,
        base_paper_version_id=context.version_id,
        operations_json=[{
            "type": "change_question_score",
            "position": 7,
            "old_question_id": items[6].question_id,
            "score_before": 10,
            "score_after": 12,
        }],
        status="pending",
    )
    multiple = AdjustmentPlanRecord(
        paper_id=context.paper_id,
        base_paper_version_id=context.version_id,
        operations_json=[
            {"type": "remove_question", "position": 7, "old_question_id": items[6].question_id},
            {"type": "remove_question", "position": 8, "old_question_id": items[7].question_id},
        ],
        status="pending",
    )
    session.add_all([single, multiple])
    session.flush()

    inherited = focus_from_pending_change(
        session,
        paper_id=context.paper_id,
        version_id=context.version_id,
        plan_id=single.id,
    )
    assert inherited == LastFocusedQuestion(
        paper_id=context.paper_id,
        paper_version_id=context.version_id,
        question_id=items[6].question_id,
        position=7,
        source="pending_change",
    )
    assert focus_from_pending_change(
        session,
        paper_id=context.paper_id,
        version_id=context.version_id,
        plan_id=multiple.id,
    ) is None


def test_bare_number_and_context_reference_need_trusted_focus():
    assert has_ambiguous_question_mutation("把第5题换掉。")
    assert has_ambiguous_question_mutation("把这道题换掉。")
    assert not has_ambiguous_question_mutation(
        "把这道题换掉。", has_trusted_focus=True,
    )

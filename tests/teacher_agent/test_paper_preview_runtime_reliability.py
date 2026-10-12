import json

import pytest
from sqlalchemy import select

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.agent.paper_change_service import PaperChangeService
from calculus_agent.models import AdjustmentPlanRecord, PaperItem
from tests.evals.curriculum_fixture import seed_eval_curriculum
from tests.evals.fixtures.paper import seed_current_paper


class SequenceBackend:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        return self.responses.pop(0)


def _tool(name, arguments=None):
    return {"message": {"role": "assistant", "content": None, "tool_calls": [{
        "id": f"call-{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments or {}, ensure_ascii=False),
        },
    }]}}


def _final(message):
    return {"message": {"role": "assistant", "content": message}}


def _paper(session):
    seed_eval_curriculum(session)
    return seed_current_paper(session, {"question_count": 10, "total_score": 100})


def _read_focus(session, context, conversation_id, position):
    result = run_teacher_agent(
        session,
        f"查看当前卷第{position}题。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=SequenceBackend(
            _tool("read_paper", {"positions": [position]}),
            _final(f"已读取第{position}题。"),
        ),
    )
    assert result.status == "completed"


@pytest.mark.parametrize("operation_type", ["replace_question", "remove_question"])
def test_successful_preview_is_terminal_and_creates_only_one_plan(
    session, operation_type,
):
    context = _paper(session)
    conversation_id = f"terminal-preview-{operation_type}"
    _read_focus(session, context, conversation_id, 7)
    operation = {
        "type": operation_type,
        "target": {"section_type": "选择题", "section_order": 1},
    }
    if operation_type == "replace_question":
        operation["difficulty_direction"] = "same"
    backend = SequenceBackend(
        _tool("preview_paper_changes", {"operations": [operation]}),
        _tool("read_paper", {"addresses": None}),
        _tool("preview_paper_changes", {"operations": [operation]}),
    )
    before = list(session.scalars(
        select(PaperItem).where(PaperItem.paper_id == context.version_id)
    ))

    result = run_teacher_agent(
        session,
        "把这道题换掉。" if operation_type == "replace_question" else "删除刚才那题。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )

    store = DatabasePendingReplacementStore(session)
    records = list(session.scalars(select(AdjustmentPlanRecord)))
    after = list(session.scalars(
        select(PaperItem).where(PaperItem.paper_id == context.version_id)
    ))
    assert result.status == "waiting_confirmation"
    assert backend.calls == 1
    assert len(records) == 1
    assert store.get_adjustment(conversation_id) == records[0].id
    assert [(item.question_id, item.score) for item in after] == [
        (item.question_id, item.score) for item in before
    ]


def test_candidate_shortage_stops_once_with_actionable_clarification(
    session, monkeypatch,
):
    context = _paper(session)
    conversation_id = "candidate-shortage"
    _read_focus(session, context, conversation_id, 8)
    original = PaperChangeService._select_replacement

    def choose_replacement(self, **kwargs):
        change = kwargs["change"]
        if (change.difficulty_direction or "same") == "same":
            return None, "same_difficulty_candidate_not_found", []
        return original(self, **kwargs)

    monkeypatch.setattr(
        PaperChangeService, "_select_replacement", choose_replacement,
    )
    backend = SequenceBackend(
        _tool("preview_paper_changes", {"operations": [{
            "type": "replace_question",
            "target": {"section_type": "选择题", "section_order": 1},
        }]}),
        _tool("preview_paper_changes", {"operations": [{
            "type": "replace_question",
            "target": {"section_type": "计算题", "section_order": 2},
            "difficulty_direction": "same",
            "preserve_knowledge_points": False,
        }]}),
    )
    before = list(session.scalars(
        select(PaperItem).where(PaperItem.paper_id == context.version_id)
    ))

    result = run_teacher_agent(
        session,
        "换一下刚才那题。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=backend,
    )

    store = DatabasePendingReplacementStore(session)
    after = list(session.scalars(
        select(PaperItem).where(PaperItem.paper_id == context.version_id)
    ))
    assert result.status == "needs_clarification"
    assert backend.calls == 1
    assert "全卷第8题" in result.message
    assert "同难度替换题" in result.message
    assert "难度稍低或稍高" in result.message
    assert store.get_adjustment(conversation_id) is None
    assert [(item.question_id, item.score) for item in after] == [
        (item.question_id, item.score) for item in before
    ]
    assert len(list(session.scalars(select(AdjustmentPlanRecord)))) == 1

    retry_backend = SequenceBackend(_tool("preview_paper_changes", {"operations": [{
        "type": "replace_question",
        "target": {"section_type": "选择题", "section_order": 1},
        "difficulty_direction": "easier",
    }]}))
    retry = run_teacher_agent(
        session,
        "把这道题换简单一点。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=retry_backend,
    )
    assert retry.status == "waiting_confirmation"
    assert retry_backend.calls == 1
    assert store.get_adjustment(conversation_id) is not None


def test_preview_still_requires_explicit_confirmation(session):
    context = _paper(session)
    conversation_id = "preview-confirm-boundary"
    _read_focus(session, context, conversation_id, 7)
    preview = run_teacher_agent(
        session,
        "删除刚才那题。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=SequenceBackend(_tool("preview_paper_changes", {"operations": [{
            "type": "remove_question",
            "target": {"section_type": "计算题", "section_order": 1},
        }]})),
    )
    before_count = session.query(PaperItem).filter_by(
        paper_id=context.version_id
    ).count()
    confirmed = run_teacher_agent(
        session,
        "确认应用。",
        conversation_id=conversation_id,
        paper_id=context.paper_id,
        version_id=context.version_id,
        backend=SequenceBackend(
            _tool("confirm_paper_changes"),
            _final("修改已确认。"),
        ),
    )

    assert preview.status == "waiting_confirmation"
    assert before_count == 10
    assert confirmed.status == "completed"
    assert confirmed.adjustment is not None
    assert session.query(PaperItem).filter_by(
        paper_id=confirmed.adjustment.new_version_id
    ).count() == 9

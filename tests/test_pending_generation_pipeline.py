"""Deterministic Pending Plan / confirmation-card state-chain regressions."""

from unittest.mock import patch

import calculus_agent.api as api
from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
    PendingGeneration,
)
from calculus_agent.agent.schemas import (
    GeneratePaperInput,
    QuestionTypePatch,
    QuestionTypeRequirement,
)
from calculus_agent.agent.tool_registry import AgentExecutionContext
from calculus_agent.agent.tools.paper_tools import (
    GeneratePaperToolResult,
    PaperSummary,
    build_structured_generation_request,
)
from calculus_agent.models import CurriculumNode, KnowledgeNode


class _Backend:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, messages, tools):
        return self.responses.pop(0)


def _tool(arguments: str, name: str = "preview_generation_plan") -> dict:
    return {"message": {"tool_calls": [{
        "id": "pending-generation", "type": "function",
        "function": {"name": name, "arguments": arguments},
    }]}}


def _final(text: str = "已更新方案。") -> dict:
    return {"message": {"content": text}}


def _scope(session) -> None:
    chapter = CurriculumNode(
        id="pending-chapter", node_type="chapter", code="3", title="第三章", sort_order=1
    )
    session.add(chapter)
    session.add(KnowledgeNode(
        id="pending-knowledge", node_type="concept", name="第三章知识点",
        normalized_name="第三章知识点", curriculum_node_id=chapter.id,
    ))
    session.flush()


def _requirements(*, fill_count: int = 2, proof_count: int = 2):
    return [
        QuestionTypeRequirement(question_type="选择题", count=4, score_each=5),
        QuestionTypeRequirement(question_type="填空题", count=fill_count, score_each=5),
        QuestionTypeRequirement(question_type="计算题", count=2, score_each=5),
        QuestionTypeRequirement(question_type="多选题", count=2, score_each=20),
        QuestionTypeRequirement(question_type="证明题", count=proof_count, score_each=10),
    ]


def _request(*, fill_count: int = 2, proof_count: int = 2) -> GeneratePaperInput:
    return GeneratePaperInput(
        paper_type="chapter_test",
        scope_names=["第三章"],
        total_score=100,
        question_type_requirements=_requirements(
            fill_count=fill_count, proof_count=proof_count
        ),
    )


def _context(session, conversation_id: str) -> AgentExecutionContext:
    return AgentExecutionContext(
        session=session,
        conversation_id=conversation_id,
        paper_id=None,
        version_id=None,
        state_store=DatabasePendingReplacementStore(session),
    )


def _seed_pending(session, conversation_id: str, request: GeneratePaperInput | None = None):
    store = DatabasePendingReplacementStore(session)
    store.set_generation(conversation_id, PendingGeneration(request=request or _request()))
    return store.get_generation(conversation_id)


def _by_type(request):
    return {item.question_type: item for item in request.question_type_requirements}


def test_pending_count_01_complete_counts_derive_total_questions(session):
    _scope(session)
    request = _request(fill_count=4, proof_count=2).model_copy(
        update={"question_count": 12, "total_score": 110}
    )

    generation_request, _, errors, _ = build_structured_generation_request(session, request)

    assert errors == []
    assert generation_request.blueprint.total_questions == 14


def test_pending_count_02_card_patch_updates_saved_plan_and_derived_count(session):
    _scope(session)
    conversation_id = "pending-card-count"
    seeded = _seed_pending(session, conversation_id)

    response = api.update_pending_generation_from_card(
        api.PendingGenerationCardPatchRequest(
            conversation_id=conversation_id,
            expected_version=seeded.pending_version,
            question_type_patches=[QuestionTypePatch(question_type="证明题", count=3)],
        ),
        session,
    )
    saved = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    types = _by_type(saved.request)

    assert response.status == "waiting_confirmation"
    assert types["证明题"].count == 3
    assert saved.request.question_count == seeded.request.question_count + 1
    assert {name: item.count for name, item in types.items() if name != "证明题"} == {
        name: item.count
        for name, item in _by_type(seeded.request).items()
        if name != "证明题"
    }
    assert saved.pending_version == seeded.pending_version + 1


def test_pending_chat_01_chat_patch_matches_confirmation_card_pipeline(session):
    _scope(session)
    chat_conversation = "pending-chat-patch"
    card_conversation = "pending-card-patch"
    _seed_pending(session, chat_conversation)
    card_pending = _seed_pending(session, card_conversation)

    final = run_teacher_agent(
        session,
        "证明题改成3道",
        conversation_id=chat_conversation,
        backend=_Backend(
            _tool('{"question_type_patches":[{"question_type":"证明题","count":3}]}'),
            _final(),
        ),
    )
    api.update_pending_generation_from_card(
        api.PendingGenerationCardPatchRequest(
            conversation_id=card_conversation,
            expected_version=card_pending.pending_version,
            question_type_patches=[QuestionTypePatch(question_type="证明题", count=3)],
        ),
        session,
    )

    chat_plan = DatabasePendingReplacementStore(session).get_generation(chat_conversation).request
    card_plan = DatabasePendingReplacementStore(session).get_generation(card_conversation).request
    assert final.status == "waiting_confirmation"
    assert chat_plan.model_dump(mode="json") == card_plan.model_dump(mode="json")


def test_pending_score_01_score_only_patch_preserves_question_count(session):
    _scope(session)
    conversation_id = "pending-score-only"
    seeded = _seed_pending(session, conversation_id)

    response = api.update_pending_generation_from_card(
        api.PendingGenerationCardPatchRequest(
            conversation_id=conversation_id,
            expected_version=seeded.pending_version,
            question_type_patches=[QuestionTypePatch(question_type="证明题", score_each=8)],
        ),
        session,
    )
    saved = DatabasePendingReplacementStore(session).get_generation(conversation_id)

    assert response.status == "waiting_confirmation"
    assert saved.request.question_count == seeded.request.question_count
    assert _by_type(saved.request)["证明题"].score_each == 8


def test_pending_confirm_01_executes_the_persisted_pending_plan(session):
    _scope(session)
    conversation_id = "pending-confirm"
    pending = _seed_pending(session, conversation_id)
    result = GeneratePaperToolResult(
        ok=True,
        paper_id="paper-from-pending",
        version_id="version-from-pending",
        summary=PaperSummary(total_questions=12, total_score=100),
    )

    with patch("calculus_agent.agent.tool_registry.generate_paper_from_input", return_value=result) as generate:
        response = api.confirm_pending_generation_from_card(
            api.PendingGenerationConfirmRequest(
                conversation_id=conversation_id,
                expected_version=pending.pending_version,
            ),
            session,
        )

    assert response.status == "completed"
    assert generate.call_args.args[1] == pending.request
    assert DatabasePendingReplacementStore(session).get_generation(conversation_id) is None


def test_pending_multi_update_01_chat_card_chat_preserves_all_state(session):
    _scope(session)
    conversation_id = "pending-multi-update"
    _seed_pending(session, conversation_id, _request(fill_count=2, proof_count=1))

    run_teacher_agent(
        session,
        "证明题改成2道",
        conversation_id=conversation_id,
        backend=_Backend(
            _tool('{"question_type_patches":[{"question_type":"证明题","count":2}]}'),
            _final(),
        ),
    )
    after_chat = DatabasePendingReplacementStore(session).get_generation(conversation_id)
    api.update_pending_generation_from_card(
        api.PendingGenerationCardPatchRequest(
            conversation_id=conversation_id,
            expected_version=after_chat.pending_version,
            question_type_patches=[QuestionTypePatch(question_type="证明题", count=3)],
        ),
        session,
    )
    final = run_teacher_agent(
        session,
        "填空题改成4道",
        conversation_id=conversation_id,
        backend=_Backend(
            _tool('{"question_type_patches":[{"question_type":"填空题","count":4}]}'),
            _final(),
        ),
    )

    saved = DatabasePendingReplacementStore(session).get_generation(conversation_id).request
    types = _by_type(saved)
    assert final.status == "waiting_confirmation", final.model_dump(mode="json")
    assert types["证明题"].count == 3
    assert types["填空题"].count == 4
    assert types["选择题"].count == 4
    assert types["计算题"].count == 2
    assert types["多选题"].count == 2
    assert saved.question_count == sum(item.count for item in saved.question_type_requirements)


def test_pending_card_version_rejects_stale_update(session):
    _scope(session)
    conversation_id = "pending-stale-card"
    pending = _seed_pending(session, conversation_id)
    api.update_pending_generation_from_card(
        api.PendingGenerationCardPatchRequest(
            conversation_id=conversation_id,
            expected_version=pending.pending_version,
            question_type_patches=[QuestionTypePatch(question_type="证明题", count=3)],
        ),
        session,
    )

    try:
        api.update_pending_generation_from_card(
            api.PendingGenerationCardPatchRequest(
                conversation_id=conversation_id,
                expected_version=pending.pending_version,
                question_type_patches=[QuestionTypePatch(question_type="填空题", count=4)],
            ),
            session,
        )
    except api.HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["code"] == "stale_pending_plan"
    else:
        raise AssertionError("stale card update unexpectedly overwrote the pending plan")


def test_session_restore_reads_the_latest_card_updated_pending_plan(session):
    _scope(session)
    conversation_id = "pending-session-restore"
    pending = _seed_pending(session, conversation_id)

    api.update_pending_generation_from_card(
        api.PendingGenerationCardPatchRequest(
            conversation_id=conversation_id,
            expected_version=pending.pending_version,
            question_type_patches=[QuestionTypePatch(question_type="证明题", count=3)],
        ),
        session,
    )
    restored = api.get_teacher_agent_session(conversation_id, session)
    types = _by_type(restored.pending_generation.request)

    assert restored.conversation_id == conversation_id
    assert types["证明题"].count == 3
    assert restored.pending_generation.request.question_count == 13
    assert restored.pending_generation.pending_version == pending.pending_version + 1

"""MERGE-01 / MERGE-02 / SCOPE-EVAL-01 regression tests.

These run against the *isolated* eval DB (never the developer's real
``calculus_agent.db``). They pin the two confirmed Bug-1 / Bug-2 fixes:

* MERGE-01: a pending plan + ``question_type_patches`` -> patch applied,
  untouched types preserved.
* MERGE-02: NO pending plan, but Working Memory holds a generation summary;
  ``question_type_patches`` must still be merged (this is the real bad case
  where the no-pending branch used to silently drop the patches).
* SCOPE-EVAL-01: an isolated session seeded with the canonical eval curriculum
  must resolve ``["第三章"]`` without errors.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from tests.conftest import create_isolated_test_session
from tests.evals.curriculum_fixture import seed_eval_curriculum

from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
    PendingGeneration,
)
from calculus_agent.agent.schemas import (
    AgentWorkingMemory,
    GeneratePaperInput,
    GenerationPlanPatch,
    QuestionTypePatch,
)
from calculus_agent.agent.tool_registry import (
    AgentExecutionContext,
    build_agent_tools,
)
from calculus_agent.agent.blueprint_adapter import (
    GenerationConstraints,
    PaperBlueprint,
    PaperGenerationRequest,
    resolve_generation_scope,
)
from calculus_agent.agent.tools.paper_tools import _scope_node_ids

CONVERSATION_ID = "test-merge"
SCOPE = ["第三章"]
TOTAL = 100

# Base plan shared by MERGE-Disposable and MERGE-02 (mirrors the MOD-02 setup):
#   选择题 4×5, 填空题 2×5, 计算题 2×10, 证明题 2×25  (total 100)
#   Only the four generatable types are used — `unknown` is NOT a normal
#   Blueprint section (it means "题型待定，需人工处理"), so it is excluded here.
BASE_REQUIREMENTS = [
    {"question_type": "选择题", "count": 4, "score_each": 5, "total_score": 20},
    {"question_type": "填空题", "count": 2, "score_each": 5, "total_score": 10},
    {"question_type": "计算题", "count": 2, "score_each": 10, "total_score": 20},
    {"question_type": "证明题", "count": 2, "score_each": 25, "total_score": 50},
]

# Patches the teacher sends (the real MOD-02 turn):
#   填空题 -> 4 道 (+10); 计算题 -> 每题 5 分 (×2, −10)  -> net total 不变
PATCHES = [
    QuestionTypePatch(question_type="填空题", count=4),
    QuestionTypePatch(question_type="计算题", score_each=5),
]


def _base_request() -> GeneratePaperInput:
    return GeneratePaperInput(
        paper_type="chapter_test",
        scope_names=list(SCOPE),
        total_score=TOTAL,
        question_type_requirements=[dict(r) for r in BASE_REQUIREMENTS],
    )


def _by_type(request) -> dict:
    return {item.question_type: item for item in request.question_type_requirements}


def _run_merge(session: Session, *, seed_pending: bool):
    """Exercise preview_generation_plan with the shared base + patches."""
    store = DatabasePendingReplacementStore(session)
    conversation_id = CONVERSATION_ID
    if seed_pending:
        store.set_generation(
            conversation_id,
            PendingGeneration(request=_base_request()),
        )
    else:
        # No pending; Working Memory carries the prior generation summary.
        store.set_memory(
            conversation_id,
            AgentWorkingMemory(generation_summary=_base_request().model_dump(mode="json")),
        )

    context = AgentExecutionContext(
        session=session,
        conversation_id=conversation_id,
        paper_id=None,
        version_id=None,
        state_store=store,
    )
    tool = build_agent_tools(context)["preview_generation_plan"]
    patch = GenerationPlanPatch(
        question_type_patches=PATCHES,
        scope_names=list(SCOPE),
        total_score=TOTAL,
    )
    result = tool.execute(patch)
    final = store.get_generation(conversation_id)
    return result, final


def _assert_modified_state(final):
    """Assert the merged outcome matches the expected MOD-02 final state."""
    assert final is not None, "no pending generation was produced"
    request = final.request
    req = _by_type(request)

    # Explicitly modified types.
    assert req["填空题"].count == 4, f"填空题.count = {req['填空题'].count}"
    assert req["计算题"].score_each == 5, f"计算题.score_each = {req['计算题'].score_each}"

    # Untouched types must be preserved exactly.
    assert req["选择题"].count == 4 and req["选择题"].score_each == 5, "选择题 changed"
    assert req["证明题"].count == 2 and req["证明题"].score_each == 25, "证明题 changed"

    # Scope and total preserved.
    assert request.scope_names == SCOPE, f"scope_names = {request.scope_names}"
    assert request.total_score == TOTAL, f"total_score = {request.total_score}"


def test_merge_01_pending_plan_patches_applied():
    """Bug 1 (pending path): patches applied, untouched fields kept."""
    session = create_isolated_test_session()
    try:
        seed_eval_curriculum(session)
        result, final = _run_merge(session, seed_pending=True)
    finally:
        session.close()

    payload = result.payload
    assert payload["ok"], f"preview not ok: {payload.get('blocking_errors')}"
    assert "scope_not_found" not in payload.get("blocking_errors", []), \
        f"scope_not_found: {payload.get('blocking_errors')}"
    _assert_modified_state(final)


def test_merge_02_no_pending_memory_summary_patches_applied():
    """Bug 1 (core bad case): no pending, Memory summary + patches must merge."""
    session = create_isolated_test_session()
    try:
        seed_eval_curriculum(session)
        result, final = _run_merge(session, seed_pending=False)
    finally:
        session.close()

    payload = result.payload
    # The whole point: previously the no-pending branch silently dropped
    # question_type_patches, so the request kept the OLD values and
    # scope_not_found hid the failure.
    assert payload["ok"], f"preview not ok: {payload.get('blocking_errors')}"
    assert "scope_not_found" not in payload.get("blocking_errors", []), \
        f"scope_not_found: {payload.get('blocking_errors')}"
    _assert_modified_state(final)


def test_scope_eval_01_isolated_curriculum_resolves_chapter_3():
    """Bug 2: isolated eval DB + canonical curriculum resolves 第三章."""
    session = create_isolated_test_session()
    try:
        # Isolated DB only — this must never touch the real calculus_agent.db.
        seed_eval_curriculum(session)

        ids, errors = _scope_node_ids(session, ["第三章"])
        assert errors == [], f"resolver errors for '第三章': {errors}"
        assert ids, "resolver returned empty ids for '第三章' (curriculum missing?)"

        request = PaperGenerationRequest(
            blueprint=PaperBlueprint(
                title="scope resolver",
                total_questions=1,
                total_score=1,
                question_type_counts={"计算题": 1},
            ),
            constraints=GenerationConstraints(scope=["第三章"]),
        )
        resolved, legacy_errors = resolve_generation_scope(session, request)
        assert legacy_errors == [], f"legacy resolver errors: {legacy_errors}"
        assert resolved is not None and resolved.constraints.scope_node_ids, \
            "legacy resolver failed to resolve '第三章'"
    finally:
        session.close()

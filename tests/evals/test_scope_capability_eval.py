from __future__ import annotations

import json
import os
from typing import Any

import pytest

from calculus_agent.application.curriculum_retrieval import (
    retrieve_curriculum_candidates,
)
from calculus_agent.knowledge.rag.embedding import LocalHashingEmbedding
from tests.evals.case_loader import EvalCase
from tests.evals.curriculum_fixture import seed_eval_curriculum
from tests.evals.runner import create_eval_session, run_case


LIVE_LLM = os.getenv("RUN_LIVE_LLM") == "1"
live_llm = pytest.mark.skipif(
    not LIVE_LLM,
    reason="Scope decision capability eval requires RUN_LIVE_LLM=1",
)

CHAPTER_1 = "第一章 函数与极限"
CHAPTER_2 = "第二章 导数与微分"
CHAPTER_3 = "第三章 微分中值定理与导数的应用"


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def _tool_calls(result: dict[str, Any]) -> list[dict[str, Any]]:
    actual = result.get("actual") or {}
    trace = actual.get("trace") or {}
    calls = trace.get("tool_calls") or []
    return [item for item in calls if isinstance(item, dict)]


def _ordered_tool_names(result: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in _tool_calls(result):
        name = item.get("tool_name") or item.get("name") or item.get("tool")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _tool_results(
    result: dict[str, Any],
    tool_name: str,
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for item in _tool_calls(result):
        name = item.get("tool_name") or item.get("name") or item.get("tool")
        if name != tool_name:
            continue
        payload = item.get("result")
        if isinstance(payload, dict):
            outputs.append(payload)
    return outputs


def _last_tool_result(
    result: dict[str, Any],
    tool_name: str,
) -> dict[str, Any]:
    outputs = _tool_results(result, tool_name)
    return outputs[-1] if outputs else {}


def _result_payload(result: dict[str, Any]) -> dict[str, Any]:
    actual = result.get("actual") or {}
    payload = actual.get("result") or {}
    return payload if isinstance(payload, dict) else {}


def _teaching_design_scope_names(
    result: dict[str, Any],
) -> list[str]:
    payload = _result_payload(result)
    design = payload.get("teaching_design") or {}
    if not isinstance(design, dict):
        return []
    content = design.get("content") or {}
    if not isinstance(content, dict):
        return []
    values = content.get("scope_names") or []
    return [value for value in values if isinstance(value, str)]


def _retrieval_snapshot(
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = _last_tool_result(
        result,
        "retrieve_curriculum_candidates",
    )
    semantic = payload.get("semantic_matches") or []
    selectable = payload.get("selectable_scopes") or []

    def compact(items: list[Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append({
                "node_id": item.get("node_id"),
                "node_type": item.get("node_type"),
                "title": item.get("title"),
                "score": item.get("similarity_score"),
                "parent_path": item.get("parent_path"),
            })
        return rows

    return {
        "query": payload.get("query"),
        "semantic_matches": compact(semantic),
        "selectable_scopes": compact(selectable),
    }


def _selection_snapshots(
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for payload in _tool_results(
        result,
        "select_teaching_scope",
    ):
        snapshots.append({
            "ok": payload.get("ok"),
            "code": payload.get("code"),
            "selected_nodes": payload.get("selected_nodes"),
            "validated_scope_names": payload.get(
                "validated_scope_names"
            ),
            "selected_knowledge_names": payload.get(
                "selected_knowledge_names"
            ),
        })
    return snapshots


def _compact_result(result: dict[str, Any]) -> str:
    actual = result.get("actual") or {}
    graders = result.get("graders") or []
    trace = actual.get("trace") or {}

    compact = {
        "case_id": result.get("case_id"),
        "passed": result.get("passed"),
        "runner_error": result.get("error"),
        "runtime_error": actual.get("error"),
        "trace_error": {
            "code": trace.get("error_code"),
            "type": trace.get("error_type"),
            "stage": trace.get("error_stage"),
            "message": trace.get("error_message"),
        },
        "status": actual.get("status"),
        "message": (actual.get("message") or "")[:500],
        "tools": _ordered_tool_names(result),
        "retrieval": _retrieval_snapshot(result),
        "selections": _selection_snapshots(result),
        "teaching_design_scope_names": (
            _teaching_design_scope_names(result)
        ),
        "grader_errors": [
            {
                "grader": grader.get("grader"),
                "errors": grader.get("errors"),
            }
            for grader in graders
            if grader.get("passed") is False
        ],
    }
    return json.dumps(
        compact,
        ensure_ascii=False,
        indent=2,
    )


def _assert_case_passed(
    result: dict[str, Any],
) -> None:
    assert result.get("error") is None, _compact_result(result)
    assert result.get("passed") is True, _compact_result(result)


def _assert_tool_order(
    result: dict[str, Any],
    expected: list[str],
) -> None:
    names = _ordered_tool_names(result)
    cursor = -1
    for tool_name in expected:
        try:
            cursor = names.index(tool_name, cursor + 1)
        except ValueError:
            raise AssertionError(
                f"missing/out-of-order tool={tool_name}\n"
                + _compact_result(result)
            ) from None


def _selectable_titles(
    result: dict[str, Any],
) -> set[str]:
    return {
        item.get("title")
        for item in _retrieval_snapshot(result)[
            "selectable_scopes"
        ]
        if item.get("title")
    }


def _assert_scope_outcome(
    result: dict[str, Any],
    *,
    expected_chapter: str,
    semantic_retrieval_required: bool,
) -> None:
    names = _ordered_tool_names(result)

    if semantic_retrieval_required:
        assert "retrieve_curriculum_candidates" in names, (
            _compact_result(result)
        )
        assert "select_teaching_scope" in names, (
            _compact_result(result)
        )

        assert expected_chapter in _selectable_titles(result), (
            "Retrieval recall miss: expected chapter never entered "
            "the LLM-selectable candidate snapshot.\n"
            + _compact_result(result)
        )

        successful = [
            item
            for item in _selection_snapshots(result)
            if item.get("ok") is True
        ]
        assert successful, (
            "Scope decision was not validated successfully.\n"
            + _compact_result(result)
        )
        assert expected_chapter in (
            successful[-1].get("validated_scope_names") or []
        ), (
            "Retrieval hit, but LLM selected the wrong curriculum scope.\n"
            + _compact_result(result)
        )

        _assert_tool_order(
            result,
            [
                "retrieve_curriculum_candidates",
                "select_teaching_scope",
                "inspect_curriculum",
                "inspect_question_bank",
                "create_teaching_design",
            ],
        )
    else:
        assert "retrieve_curriculum_candidates" not in names, (
            "Explicit chapter scope should use deterministic resolution "
            "instead of semantic retrieval.\n"
            + _compact_result(result)
        )
        assert "select_teaching_scope" not in names, (
            _compact_result(result)
        )
        _assert_tool_order(
            result,
            [
                "inspect_curriculum",
                "inspect_question_bank",
                "create_teaching_design",
            ],
        )

    scope_names = _teaching_design_scope_names(result)
    assert expected_chapter in scope_names, (
        "Final TeachingDesign scope is wrong even though upstream "
        "scope processing completed.\n"
        + _compact_result(result)
    )

    assert "confirm_teaching_design" not in names, (
        _compact_result(result)
    )
    assert "confirm_generation" not in names, (
        _compact_result(result)
    )


# ---------------------------------------------------------------------------
# Layer A: deterministic retrieval recall
# ---------------------------------------------------------------------------


def _retrieve(
    query: str,
    *,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    session = create_eval_session()
    try:
        seed_eval_curriculum(session)
        candidates = retrieve_curriculum_candidates(
            session,
            query=query,
            top_k=top_k,
            embedding_provider=LocalHashingEmbedding(
                dim=2048
            ),
        )
        return [
            item.model_dump(mode="json")
            for item in candidates
        ]
    finally:
        session.close()


def _retrieval_failure_message(
    query: str,
    candidates: list[dict[str, Any]],
) -> str:
    return json.dumps(
        {
            "query": query,
            "ranked_candidates": [
                {
                    "rank": index,
                    "node_id": item.get("node_id"),
                    "node_type": item.get("node_type"),
                    "title": item.get("title"),
                    "score": item.get("similarity_score"),
                    "parent_path": item.get("parent_path"),
                }
                for index, item in enumerate(
                    candidates,
                    start=1,
                )
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def test_scope_retrieval_generic_limit_query_has_high_recall() -> None:
    """
    "极限不好" is intentionally NOT unique.

    Chapter 1 directly teaches limits, while chapter 3 contains L'Hopital and
    derivative-based limit techniques. Retrieval should preserve both plausible
    curriculum paths rather than prematurely resolving to one chapter.
    """
    query = "学生极限不好"
    candidates = _retrieve(query, top_k=8)

    ids = {
        item.get("node_id")
        for item in candidates
    }

    assert "eval-chapter-1" in ids, (
        _retrieval_failure_message(
            query,
            candidates,
        )
    )

    chapter3_path = [
        item
        for item in candidates
        if (
            item.get("node_id") == "eval-chapter-3"
            or CHAPTER_3 in (item.get("parent_path") or [])
        )
        and float(item.get("similarity_score") or 0) > 0
    ]
    assert chapter3_path, (
        "High-recall failure: generic limit weakness should have a genuine "
        "positive semantic path into chapter 3 through L'Hopital / limit "
        "applications, not merely a zero-score top-k filler.\n"
        + _retrieval_failure_message(
            query,
            candidates,
        )
    )


def test_scope_retrieval_lhopital_query_recalls_chapter_three() -> None:
    query = "学生不会洛必达法则"
    candidates = _retrieve(query)

    ids = {
        item.get("node_id")
        for item in candidates
    }
    assert "eval-chapter-3" in ids, (
        _retrieval_failure_message(
            query,
            candidates,
        )
    )


# ---------------------------------------------------------------------------
# Layer B: live LLM scope decision capability
# ---------------------------------------------------------------------------


@live_llm
def test_scope_explicit_chapter_reaches_teaching_design() -> None:
    case = EvalCase(
        id="SCOPE-CAP-EXPLICIT-01",
        category="scope_capability",
        title="Explicit chapter reaches a pending TeachingDesign",
        turns=[
            {
                "user": "高数第一章，帮我设计一个复习方案。",
            }
        ],
        expected={
            "status": "waiting_confirmation",
        },
        graders=[
            {"type": "state"},
        ],
    )

    result = run_case(case)

    _assert_case_passed(result)
    # Outcome > tool path: semantic retrieval is redundant for an explicit
    # chapter, but that is a latency/efficiency concern rather than the core
    # capability contract. The required outcome is a real pending design in the
    # teacher-requested chapter, without an extra "shall I create it?" turn.
    names = _ordered_tool_names(result)
    scope_names = _teaching_design_scope_names(result)

    assert "inspect_curriculum" in names, _compact_result(result)
    assert "inspect_question_bank" in names, _compact_result(result)
    assert "create_teaching_design" in names, (
        "Teacher explicitly requested a design. After scope + environment "
        "inspection the Agent must create the pending TeachingDesign in the "
        "same turn instead of asking for a second pre-creation confirmation.\n"
        + _compact_result(result)
    )
    assert CHAPTER_1 in scope_names, _compact_result(result)
    assert "confirm_teaching_design" not in names, _compact_result(result)
    assert "confirm_generation" not in names, _compact_result(result)


@live_llm
def test_scope_generic_limit_topic_preserves_multiple_plausible_paths() -> None:
    """
    Generic "极限不好" is a recall case, not a forced-clarification case.

    Retrieval should preserve both plausible paths:
    - 第一章 函数与极限
    - 第三章中的洛必达 / 极限应用

    The semantic decision may still choose chapter 1 when it is clearly more
    relevant. What is forbidden is losing the chapter-3 path during retrieval,
    or choosing an unrelated chapter such as chapter 2.
    """
    case = EvalCase(
        id="SCOPE-CAP-LIMIT-RECALL-01",
        category="scope_capability",
        title="Generic limit weakness preserves plausible curriculum paths",
        turns=[
            {
                "user": "学生极限不好，帮我设计复习方案。",
            }
        ],
        expected={},
        graders=[{"type": "state"}],
    )

    result = run_case(case)

    _assert_case_passed(result)

    names = _ordered_tool_names(result)
    assert "retrieve_curriculum_candidates" in names, (
        _compact_result(result)
    )

    selectable = _selectable_titles(result)
    assert CHAPTER_1 in selectable, (
        "Chapter 1 missing from generic limit candidates.\n"
        + _compact_result(result)
    )
    assert CHAPTER_3 in selectable, (
        "Chapter 3 missing from generic limit candidates despite a real "
        "L'Hopital→limit semantic path.\n"
        + _compact_result(result)
    )

    # If the LLM chooses a scope, it must stay inside the two genuinely
    # plausible paths for this broad topic. Chapter 2 is not an acceptable
    # collapse merely because it is present as a low/zero-score top-k row.
    successful = [
        item
        for item in _selection_snapshots(result)
        if item.get("ok") is True
    ]
    for selection in successful:
        selected = set(
            selection.get("validated_scope_names") or []
        )
        assert selected
        assert selected.issubset({CHAPTER_1, CHAPTER_3}), (
            "Generic limit topic was collapsed to an unrelated scope.\n"
            + _compact_result(result)
        )

    design_scopes = set(
        _teaching_design_scope_names(result)
    )
    if design_scopes:
        assert design_scopes.issubset({CHAPTER_1, CHAPTER_3}), (
            _compact_result(result)
        )


@live_llm
def test_scope_function_limit_topic_resolves_to_chapter_one() -> None:
    case = EvalCase(
        id="SCOPE-CAP-FUNCTION-LIMIT-01",
        category="scope_capability",
        title="Function-limit topic resolves to chapter one",
        turns=[
            {
                "user": "学生函数的极限这部分掌握不好，帮我设计复习方案。",
            }
        ],
        expected={
            "status": "waiting_confirmation",
        },
        graders=[
            {"type": "state"},
        ],
    )

    result = run_case(case)

    _assert_case_passed(result)
    _assert_scope_outcome(
        result,
        expected_chapter=CHAPTER_1,
        semantic_retrieval_required=True,
    )


@live_llm
def test_scope_lhopital_topic_resolves_to_chapter_three() -> None:
    case = EvalCase(
        id="SCOPE-CAP-LHOPITAL-01",
        category="scope_capability",
        title="L'Hopital weakness resolves to owning chapter",
        turns=[
            {
                "user": "学生洛必达法则总不会，帮我设计复习方案。",
            }
        ],
        expected={
            "status": "waiting_confirmation",
        },
        graders=[
            {"type": "state"},
        ],
    )

    result = run_case(case)

    _assert_case_passed(result)
    _assert_scope_outcome(
        result,
        expected_chapter=CHAPTER_3,
        semantic_retrieval_required=True,
    )


@live_llm
def test_scope_out_of_curriculum_topic_must_abstain() -> None:
    """
    A linear-algebra topic must not be coerced into the nearest calculus scope.
    """
    case = EvalCase(
        id="SCOPE-CAP-ABSTAIN-01",
        category="scope_capability",
        title="Out-of-curriculum topic does not fabricate a scope",
        turns=[
            {
                "user": "学生矩阵特征值不会，帮我设计复习方案。",
            }
        ],
        expected={},
        graders=[{"type": "state"}],
    )

    result = run_case(case)

    _assert_case_passed(result)

    names = _ordered_tool_names(result)
    status = (result.get("actual") or {}).get("status")
    assert status in {"needs_clarification", "completed"}, (
        _compact_result(result)
    )
    assert "retrieve_curriculum_candidates" in names, (
        _compact_result(result)
    )
    assert "create_teaching_design" not in names, (
        "Out-of-curriculum input must not create a TeachingDesign.\n"
        + _compact_result(result)
    )
    assert not _teaching_design_scope_names(result), (
        _compact_result(result)
    )

    successful_selections = [
        payload
        for payload in _selection_snapshots(result)
        if payload.get("ok") is True
    ]
    assert not successful_selections, (
        "Agent incorrectly committed the nearest retrieved calculus "
        "candidate as a valid scope.\n"
        + _compact_result(result)
    )

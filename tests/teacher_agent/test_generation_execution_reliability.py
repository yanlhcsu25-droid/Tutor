"""Offline regressions for the phase-5 generation execution contract."""

from __future__ import annotations

import copy
import json

from calculus_agent.agent.agent import run_teacher_agent
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.agent.trace_log import AgentTraceRecorder
from calculus_agent.runtime.request_guards import extract_generation_constraint_contract
from tests.teacher_agent.test_teacher_agent_structured_generation import (
    _candidate as seed_candidate,
    _scope as seed_scope,
)


PQ002_REQUEST = "第三章出100分测试：选择4、填空4、计算2、解答2、证明2。"
EXPECTED_TYPES = {"选择题": 4, "填空题": 4, "计算题": 4, "证明题": 2}


def _tool(arguments: dict) -> dict:
    return {
        "message": {
            "tool_calls": [{
                "id": "prepare",
                "type": "function",
                "function": {
                    "name": "prepare_generation_plan",
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }],
        },
    }


def _final(text: str) -> dict:
    return {"message": {"content": text}}


def _scope(session, *, with_questions: bool = False) -> None:
    chapter = seed_scope(session)
    chapter.code = "3"
    chapter.title = "中值定理与导数应用"
    session.flush()
    if with_questions:
        number = 1
        for question_type, count in EXPECTED_TYPES.items():
            for _ in range(count):
                seed_candidate(
                    session,
                    number=number,
                    question_type=question_type,
                    knowledge_id="structured-limit",
                )
                number += 1


class _Backend:
    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.requests: list[tuple[list[dict], list[dict]]] = []

    def complete(self, messages, tools):
        self.requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        return self.responses.pop(0)


def test_pq002_constraint_contract_preserves_raw_and_canonical_aliases():
    contract = extract_generation_constraint_contract(PQ002_REQUEST)

    assert contract["original_request"] == PQ002_REQUEST
    assert contract["raw"]["question_type_requirements"] == [
        {"question_type": "选择", "count": 4},
        {"question_type": "填空", "count": 4},
        {"question_type": "计算", "count": 2},
        {"question_type": "解答", "count": 2},
        {"question_type": "证明", "count": 2},
    ]
    assert contract["canonical"]["question_count"] == 14
    assert contract["canonical"]["total_score"] == 100
    assert {
        item["question_type"]: item["count"]
        for item in contract["canonical"]["question_type_requirements"]
    } == EXPECTED_TYPES


def test_first_prepare_receives_known_canonical_hard_constraints(session):
    _scope(session, with_questions=True)
    requirements = [
        {"question_type": question_type, "count": count}
        for question_type, count in EXPECTED_TYPES.items()
    ]
    backend = _Backend(
        _tool({
            "paper_type": "chapter_test",
            "scope_names": ["第三章"],
            "question_count": 14,
            "total_score": 100,
            "question_type_requirements": requirements,
        }),
        _final("请确认方案。"),
    )

    recorder = AgentTraceRecorder()
    result = run_teacher_agent(
        session,
        PQ002_REQUEST,
        conversation_id="phase5-first-prepare",
        backend=backend,
        trace_recorder=recorder,
    )

    first_prompt = json.dumps(backend.requests[0][0], ensure_ascii=False)
    pending = DatabasePendingReplacementStore(session).get_generation(
        "phase5-first-prepare"
    )
    assert "confirmed_generation_constraints" in first_prompt
    assert recorder._record["tool_calls"][0]["result"].get("ok") is True, recorder._record["tool_calls"][0]["result"]
    assert result.status == "waiting_confirmation", result
    assert '\\"question_type\\": \\"计算题\\", \\"count\\": 4' in first_prompt
    assert pending is not None
    assert {
        item.question_type: item.count
        for item in pending.request.question_type_requirements
    } == EXPECTED_TYPES


def test_same_constraint_error_stops_after_one_agent_recovery(session):
    _scope(session)
    invalid = {
        "paper_type": "chapter_test",
        "scope_names": ["第三章"],
        "total_score": 100,
        "question_type_requirements": [{"question_type": "选择题", "count": 4}],
    }
    backend = _Backend(_tool(invalid), _tool(invalid))

    result = run_teacher_agent(
        session,
        PQ002_REQUEST,
        conversation_id="phase5-repeated-mismatch",
        backend=backend,
        max_tool_rounds=8,
    )

    assert len(backend.requests) == 2
    assert result.status == "failed"
    assert "explicit_constraint_mismatch" in result.blocking_errors
    assert "agent_tool_round_limit" not in result.blocking_errors


def test_generation_prose_only_cannot_complete(session):
    _scope(session)
    backend = _Backend(
        _final("我建议按四类题型组织。"),
        _final("方案已经考虑好了。"),
    )

    result = run_teacher_agent(
        session,
        PQ002_REQUEST,
        conversation_id="phase5-prose-only",
        backend=backend,
    )

    assert result.status == "failed"
    assert "generation_plan_required" in result.blocking_errors
    assert DatabasePendingReplacementStore(session).get_generation(
        "phase5-prose-only"
    ) is None


def test_pq001_and_pq003_request_parsing_remains_non_destructive():
    pq001 = extract_generation_constraint_contract(
        "从第一章出6题，共60分，不要跨章节。"
    )
    pq003 = extract_generation_constraint_contract(
        "只用前三章题目组一份卷，不要使用后续章节。"
    )

    assert pq001["canonical"]["question_count"] == 6
    assert pq001["canonical"]["total_score"] == 60
    assert pq003 is None

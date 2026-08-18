"""SCOPE-01 regression eval.

Goal (per user diagnosis): verify the bottom-layer scope resolver can actually
resolve the teacher-facing label "第三章" against the persisted curriculum tree,
*without* going through the Agent.

The real `calculus_agent.db` stores chapter 3 as:
    curriculum_node.code = "三"
    curriculum_node.title = "微分中值定理与导数的应用"
    (one knowledge_node child mounted via curriculum_node_id)

The resolver must therefore map "第三章" -> numeral "三" -> code "三", and must
NOT collapse "chapter exists but has no eligible questions" into scope_not_found.

Run:
    uv run python -m pytest tests/test_scope_resolve_chapter3.py -q
Or against a specific db:
    CALCULUS_AGENT_SCOPE_TEST_DB=/path/to.db uv run python -m pytest tests/test_scope_resolve_chapter3.py -q
"""

from __future__ import annotations

import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from tests.integration_db import configured_integration_db_path

from calculus_agent.agent.blueprint_adapter import (
    PaperBlueprint,
    PaperGenerationRequest,
    GenerationConstraints,
    resolve_generation_scope,
)
from calculus_agent.agent.tools.paper_tools import _scope_node_ids

# SCOPE-01 is a local-DB integration test. Execution is explicitly opt-in:
# an accidental repository calculus_agent.db must never activate it.
pytestmark = pytest.mark.integration


def _make_session() -> Session:
    db_path = configured_integration_db_path()
    url = f"sqlite:///{db_path}"
    factory = sessionmaker(bind=create_engine(url, connect_args={"check_same_thread": False}))
    return factory()


def test_resolve_chapter_3():
    """"第三章" must resolve to a non-empty list of knowledge node ids (no scope_not_found)."""
    session = _make_session()
    try:
        ids, errors = _scope_node_ids(session, ["第三章"])
    finally:
        session.close()

    print("\n[SCOPE-01] _scope_node_ids(['第三章']) ->")
    print(f"    ids={ids}")
    print(f"    errors={errors}")
    assert errors == [], f"resolver returned errors for '第三章': {errors}"
    assert ids, "resolver returned empty ids for '第三章' (chapter exists in DB)"


def test_resolve_chapter_3_legacy_resolver():
    """The legacy resolve_generation_scope path must also resolve '第三章'."""
    session = _make_session()
    try:
        request = PaperGenerationRequest(
            blueprint=PaperBlueprint(
                title="scope resolver",
                total_questions=1,
                total_score=1,
                question_type_counts={"计算题": 1},
            ),
            constraints=GenerationConstraints(scope=["第三章"]),
        )
        resolved, errors = resolve_generation_scope(session, request)
    finally:
        session.close()

    print("\n[SCOPE-01-LEGACY] resolve_generation_scope(scope=['第三章']) ->")
    print(f"    errors={errors}")
    print(f"    scope_node_ids={resolved.constraints.scope_node_ids if resolved else None}")
    assert errors == [], f"legacy resolver returned errors for '第三章': {errors}"
    assert resolved is not None and resolved.constraints.scope_node_ids, \
        "legacy resolver failed to resolve '第三章'"


def test_resolve_chapter_3_by_title_and_numeral():
    """Both the Chinese numeral title form and '第三章' should reach the same chapter.

    Guards against the previous brittle behaviour where only an exact '第三章'
    literal in title/code would match.
    """
    session = _make_session()
    try:
        ids_title, err_title = _scope_node_ids(session, ["微分中值定理与导数的应用"])
        ids_numeral, err_numeral = _scope_node_ids(session, ["第三章"])
    finally:
        session.close()

    assert err_title == [], f"title form failed: {err_title}"
    assert err_numeral == [], f"numeral form failed: {err_numeral}"
    assert set(ids_title) == set(ids_numeral), \
        "title form and '第三章' form resolved to different nodes"

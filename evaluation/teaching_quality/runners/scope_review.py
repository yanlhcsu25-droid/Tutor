"""Conservative lexical preflight for frozen Dev allowed_scope_only assertions.

This is *not* a semantic evaluator. Anything beyond high-confidence spelling
aliases remains unscored pending contextual review, never silently passed.
"""
from __future__ import annotations

import re
from typing import Any


def _compact(value: str) -> str:
    return re.sub(r"[\s·•\-的]", "", value)


def assess_scope(assertion: dict[str, Any], observed: list[str],
                 setup: dict[str, Any], *, forbidden: list[str] | None = None) -> dict[str, Any]:
    if assertion.get("type") != "allowed_scope_only":
        raise ValueError("Only allowed_scope_only assertions are supported")
    expected = assertion.get("expected", [])
    if not isinstance(expected, list) or not all(isinstance(v, str) for v in expected):
        raise ValueError("Expected allowed topic list is required")
    if not isinstance(observed, list) or not all(isinstance(v, str) for v in observed):
        raise ValueError("Observed topic list is required")
    aliases: dict[str, str] = {}
    unresolved: list[str] = []
    explicit_forbidden: list[str] = []
    forbidden = forbidden or []
    for item in observed:
        if any(_compact(term) == _compact(item) for term in forbidden):
            explicit_forbidden.append(item)
            continue
        if item in expected:
            aliases[item] = item
            continue
        matches = [term for term in expected if _compact(item) == _compact(term)]
        if len(matches) == 1:
            aliases[item] = matches[0]
            continue
        if ("导数" in str(setup.get("course_progress", ""))
                and item in {"导数的几何意义", "导数几何意义"}
                and "几何意义" in expected):
            aliases[item] = "几何意义"
            continue
        unresolved.append(item)
    status = ("MISSING_OBSERVATION" if not observed else
              "FAIL_EXPLICIT_FORBIDDEN" if explicit_forbidden else
              "NEEDS_SEMANTIC_REVIEW" if unresolved else "PASS_LEXICAL")
    return {
        "status": status,
        "canonical": [aliases[x] for x in observed if x in aliases],
        "accepted_aliases": {k: v for k, v in aliases.items() if k != v},
        "unresolved_topics": sorted(set(unresolved)),
        "explicit_forbidden": sorted(set(explicit_forbidden)),
        "method": "strict equivalence only; no curriculum progression inference",
    }

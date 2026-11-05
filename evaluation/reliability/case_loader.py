from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class EvalCase:
    id: str
    category: str
    title: str
    turns: list[dict[str, Any]] = field(default_factory=list)
    expected: dict[str, Any] = field(default_factory=dict)
    forbidden: dict[str, Any] = field(default_factory=dict)
    preserve: list[str] = field(default_factory=list)
    graders: list[dict[str, Any]] = field(default_factory=list)

    setup: dict[str, Any] = field(default_factory=dict)
    backend: dict[str, Any] = field(default_factory=dict)
    invoke: dict[str, Any] = field(default_factory=dict)
    business_rules: dict[str, Any] = field(default_factory=dict)

    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalSuite:
    version: str
    name: str
    description: str
    defaults: dict[str, Any]
    cases: list[EvalCase]


def load_eval_suite(path: str | Path) -> EvalSuite:
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Eval YAML not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        payload = yaml.safe_load(f)

    if not isinstance(payload, dict):
        raise ValueError("Eval YAML root must be a mapping")

    suite_config = payload.get("suite", {})
    raw_cases = payload.get("cases", [])

    if not isinstance(raw_cases, list):
        raise ValueError("'cases' must be a list")

    cases: list[EvalCase] = []

    seen_ids: set[str] = set()

    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid case: {raw!r}")

        case_id = raw.get("id")

        if not case_id:
            raise ValueError("Every eval case must have an id")

        if case_id in seen_ids:
            raise ValueError(f"Duplicate eval case id: {case_id}")

        seen_ids.add(case_id)

        graders = raw.get("graders", [])

        normalized_graders = []

        for grader in graders:
            if isinstance(grader, str):
                normalized_graders.append({"type": grader})
            elif isinstance(grader, dict):
                normalized_graders.append(grader)
            else:
                raise ValueError(
                    f"{case_id}: invalid grader definition: {grader!r}"
                )

        cases.append(
            EvalCase(
                id=case_id,
                category=raw.get("category", "unknown"),
                title=raw.get("title", case_id),
                turns=raw.get("turns", []),
                expected=raw.get("expected", {}),
                forbidden=raw.get("forbidden", {}),
                preserve=raw.get("preserve", []),
                graders=normalized_graders,
                setup=raw.get("setup", {}),
                backend=raw.get("backend", {}),
                invoke=raw.get("invoke", {}),
                business_rules=raw.get("business_rules", {}),
                raw=raw,
            )
        )

    return EvalSuite(
        version=str(payload.get("version", "0.1")),
        name=suite_config.get("name", "teacher_agent_eval"),
        description=suite_config.get("description", ""),
        defaults=payload.get("defaults", {}),
        cases=cases,
    )


def filter_cases(
    suite: EvalSuite,
    *,
    case_ids: set[str] | None = None,
    categories: set[str] | None = None,
) -> list[EvalCase]:
    result = suite.cases

    if case_ids:
        result = [case for case in result if case.id in case_ids]

    if categories:
        result = [
            case for case in result
            if case.category in categories
        ]

    return result
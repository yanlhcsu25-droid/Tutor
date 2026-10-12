"""Deterministic arithmetic and persisted-content consistency checks."""

from __future__ import annotations

import re
from collections import Counter
from math import isclose
from typing import Any

_QUESTION_CLAIM = re.compile(r"(?:共|总共|一共|包含|有)\s*(\d+)\s*(?:道)?题")
_SCORE_CLAIM = re.compile(r"(?:总分|满分)\s*(?:为|是|共)?\s*(\d+(?:\.\d+)?)\s*分")
_TYPE_CLAIM = re.compile(r"(选择题|填空题|计算题|证明题)\s*(?:共|有)?\s*(\d+)\s*(?:道|题)")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_plan(plan: Any, path: str) -> list[str]:
    if not isinstance(plan, dict):
        return []
    requirements = plan.get("question_type_requirements")
    if not isinstance(requirements, list) or not requirements:
        return []
    errors: list[str] = []
    valid = [item for item in requirements if isinstance(item, dict)]
    counts = [item.get("count") for item in valid]
    if len(valid) != len(requirements) or not all(_number(value) for value in counts):
        errors.append(f"{path}: invalid question_type_requirements")
        return errors

    question_count = plan.get("question_count")
    count_sum = sum(counts)
    if _number(question_count) and count_sum != question_count:
        errors.append(
            f"{path}: section count sum={count_sum}, question_count={question_count}"
        )

    section_scores: list[float] = []
    for item in valid:
        count = item.get("count")
        score_each = item.get("score_each")
        section_total = item.get("total_score")
        if _number(score_each) and _number(section_total):
            calculated = count * score_each
            if not isclose(calculated, section_total, abs_tol=0.01):
                errors.append(
                    f"{path}.{item.get('question_type', 'unknown')}: "
                    f"count*score_each={calculated}, total_score={section_total}"
                )
        if _number(section_total):
            section_scores.append(float(section_total))
        elif _number(score_each):
            section_scores.append(float(count * score_each))

    total_score = plan.get("total_score")
    if _number(total_score) and len(section_scores) == len(valid):
        calculated_total = sum(section_scores)
        if not isclose(calculated_total, total_score, abs_tol=0.01):
            errors.append(
                f"{path}: section score sum={calculated_total}, total_score={total_score}"
            )
    return errors


def _paper_errors(
    snapshot: dict[str, Any], *, enforce_single_chapter: bool = False,
) -> list[str]:
    paper_state = snapshot.get("paper") or {}
    papers = paper_state.get("paper") or []
    items = paper_state.get("paper_item") or []
    question_chapters = paper_state.get("question_chapters") or {}
    errors: list[str] = []
    for paper in papers:
        paper_id = paper.get("id")
        paper_items = [item for item in items if item.get("paper_id") == paper_id]
        question_ids = [item.get("question_id") for item in paper_items]
        if len(question_ids) != len(set(question_ids)):
            errors.append(f"paper {paper_id}: duplicate question ids")
        if enforce_single_chapter:
            chapters = {
                question_chapters.get(question_id)
                for question_id in question_ids
                if question_chapters.get(question_id) is not None
            }
            if len(chapters) > 1:
                errors.append(f"paper {paper_id}: questions cross chapters {sorted(chapters)}")
        scores = [item.get("score") for item in paper_items]
        if scores and all(_number(score) for score in scores) and _number(paper.get("total_score")):
            actual_score = sum(scores)
            if not isclose(actual_score, paper["total_score"], abs_tol=0.01):
                errors.append(
                    f"paper {paper_id}: item score sum={actual_score}, "
                    f"paper total_score={paper['total_score']}"
                )
    return errors


def _latest_paper(snapshot: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    paper_state = snapshot.get("paper") or {}
    papers = paper_state.get("paper") or []
    items = paper_state.get("paper_item") or []
    pointer = paper_state.get("active_pointer") or [None, None]
    target_id = pointer[1] or pointer[0]
    paper = next((row for row in papers if row.get("id") == target_id), None)
    if paper is None and papers:
        paper = max(papers, key=lambda row: (row.get("version") or 0, str(row.get("id"))))
    if paper is None:
        return None, []
    return paper, [item for item in items if item.get("paper_id") == paper.get("id")]


def _claim_errors(message: str, snapshot: dict[str, Any]) -> list[str]:
    # Added by grader fact fix: read-only claims cannot be scored as preview.
    if not (snapshot.get("preview_paper_change") or {}):
        paper, items = _latest_paper(snapshot.get("business_state") or {})
        if paper is None:
            return []
        from tests.evals.graders.response_fact_parser import read_only_claim_errors

        distribution = Counter(str(item.get("section")) for item in items)
        return read_only_claim_errors(
            message,
            expected_count=len(items),
            expected_score=paper.get("total_score"),
            persisted_type_counts=distribution,
        )
    preview = snapshot.get("preview_paper_change") or {}
    paper, items = _latest_paper(snapshot.get("business_state") or {})
    if paper is None and not preview:
        return []
    errors: list[str] = []
    expected_count = preview.get("question_count") if preview else len(items)
    expected_score = preview.get("total_score") if preview else paper.get("total_score")
    source = "preview" if preview else "persisted"
    def claim_target(match: re.Match[str], persisted: Any, planned: Any) -> tuple[Any, str]:
        context = message[max(0, match.start() - 24):match.start()]
        before_markers = list(
            re.finditer(r"(?:删除|修改|变更|调整)?前|原(?:试卷|卷)?", context)
        )
        after_markers = list(
            re.finditer(r"(?:删除|修改|变更|调整)?后|预览", context)
        )
        if preview and before_markers and (
            not after_markers or before_markers[-1].end() > after_markers[-1].end()
        ):
            return persisted, "persisted"
        return planned, source

    for question_match in _QUESTION_CLAIM.finditer(message):
        target, target_source = claim_target(question_match, len(items), expected_count)
        if int(question_match.group(1)) != target:
            errors.append(
                f"reply question count={question_match.group(1)}, "
                f"{target_source}={target}"
            )
    for score_match in _SCORE_CLAIM.finditer(message):
        target, target_source = claim_target(
            score_match, paper.get("total_score"), expected_score,
        )
        if not isclose(float(score_match.group(1)), float(target or 0), abs_tol=0.01):
            errors.append(
                f"reply total score={score_match.group(1)}, "
                f"{target_source}={target}"
            )
    distribution = Counter(str(item.get("section")) for item in items)
    for question_type, count in _TYPE_CLAIM.findall(message):
        if int(count) != distribution[question_type]:
            errors.append(
                f"reply {question_type} count={count}, persisted={distribution[question_type]}"
            )
    return errors


def _read_item_errors(turn: dict[str, Any], message: str) -> list[str]:
    calls = ((turn.get("state") or {}).get("trace") or {}).get("tool_calls") or []
    questions: list[dict[str, Any]] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        name = call.get("tool_name") or call.get("name") or call.get("tool")
        result = call.get("result") or call.get("output_json") or {}
        if name == "read_paper" and isinstance(result, dict):
            questions.extend(
                item for item in result.get("questions") or [] if isinstance(item, dict)
            )
    errors = []
    normalized = re.sub(r"\s+", "", message)
    for question in questions:
        content = str(question.get("content") or "").strip()
        if content and re.sub(r"\s+", "", content) not in normalized:
            errors.append(
                f"reply does not reproduce read question content at "
                f"position={question.get('position')}: {content!r}"
            )
    return errors


def _teaching_design_errors(snapshot: dict[str, Any]) -> list[str]:
    rows = (snapshot.get("teaching_design") or {}).get("teaching_design_version") or []
    errors: list[str] = []
    for row in rows:
        content = row.get("design_json") or {}
        errors.extend(_check_plan(
            content.get("assessment_plan"),
            f"teaching_design {row.get('id')}.assessment_plan",
        ))
    return errors


def grade_content_consistency(case, actual: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    seen: set[int] = set()
    for name in ("generation", "pending_generation"):
        plan = actual.get(name)
        if isinstance(plan, dict) and id(plan) not in seen:
            seen.add(id(plan))
            errors.extend(_check_plan(plan, name))

    turns = actual.get("evaluated_turns") or []
    preserve = set(getattr(case, "preserve", []) or [])
    business_rules = getattr(case, "business_rules", {}) or {}
    enforce_single_chapter = bool(
        {"no_cross_chapter", "no-cross-chapter"} & preserve
        or business_rules.get("no_cross_chapter")
    )
    enforce_read_content = bool(business_rules.get("require_read_content_grounding"))
    for turn in turns:
        snapshot = turn.get("business_state_after") or {}
        prefix = f"turn {turn.get('turn')}"
        errors.extend(
            f"{prefix}: {error}"
            for error in _paper_errors(
                snapshot, enforce_single_chapter=enforce_single_chapter,
            )
        )
        errors.extend(f"{prefix}: {error}" for error in _teaching_design_errors(snapshot))
        message = str((turn.get("result") or {}).get("message") or "")
        errors.extend(
            f"{prefix}: {error}"
            for error in _claim_errors(message, {
                "business_state": snapshot,
                **(turn.get("state") or {}),
            })
        )
        if enforce_read_content:
            errors.extend(
                f"{prefix}: {error}" for error in _read_item_errors(turn, message)
            )

    return {
        "grader": "content_consistency",
        "passed": not errors,
        "errors": errors,
    }

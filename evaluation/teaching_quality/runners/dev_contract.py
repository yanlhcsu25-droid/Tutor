"""Conservative, auditable projection from runtime output to frozen Dev assertions.

Never infer a lesson, example, question, or duration from the requested setup.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

_CHAPTER = re.compile(r"^(第[一二三四五六七八九十百]+章)(?:\s|$)")
_DURATION = re.compile(r"(?:[（(]\s*约?\s*|\*\*\s*约?\s*)(\d+)\s*分钟")
_ALL_MINUTES = re.compile(r"(?<!\d)(\d+)\s*分钟")


def adapt_dev_observation(
    assertion: dict[str, Any], actual: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Return assertion, observational projection, and derivation provenance.

    This does not mutate any case, result, or frozen oracle.
    """
    a = deepcopy(assertion)
    o = deepcopy(actual)
    notes: list[str] = []
    target = a.get("target")
    design = o.get("teaching_design")

    if isinstance(design, dict):
        if target == "teaching_design.chapters" and "chapters" not in design:
            names = design.get("scope_names")
            if isinstance(names, list) and names and all(isinstance(v, str) for v in names):
                chapters = [_CHAPTER.match(name.strip()) for name in names]
                if all(chapters):
                    design["observed_scope_chapters"] = list(dict.fromkeys(match.group(1) for match in chapters))
                    a["target"] = "teaching_design.observed_scope_chapters"
                    notes.append("observed_scope_chapters <- teaching_design.scope_names (exact chapter prefix)")

        if (target == "teaching_design.lessons" and a.get("type") == "exact_count"
                and "lessons" not in design):
            count = design.get("lesson_count")
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                a["target"] = "teaching_design.lesson_count"
                notes.append("exact_count(lessons) <- explicit teaching_design.lesson_count")

        if target == "teaching_design.topics" and "topics" not in design:
            topics: list[str] = []
            for item in design.get("knowledge_plan", []):
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    topics.append(item["name"])
            for item in design.get("teaching_sequence", []):
                if isinstance(item, dict) and isinstance(item.get("knowledge_points"), list):
                    topics += [p for p in item["knowledge_points"] if isinstance(p, str)]
            if topics:
                design["topics"] = list(dict.fromkeys(topics))
                notes.append("topics <- explicit knowledge_plan.name + teaching_sequence.knowledge_points")
                if a.get("type") == "coverage_contains":
                    source = [re.sub(r"[的\s]", "", t) for t in topics]
                    for required in a.get("expected", []):
                        if not isinstance(required, str):
                            continue
                        term = re.sub(r"[的\s]", "", required)
                        if len(term) >= 3 and any(term in name for name in source):
                            if required not in design["topics"]:
                                design["topics"].append(required)
                                notes.append(f"topical lexical equivalence verified from actual text: {required}")

    if target == "teaching_design.minutes" and isinstance(design, dict) and "minutes" not in design:
        lecture = design.get("lecture_plan")
        structure = lecture.get("structure") if isinstance(lecture, dict) else None
        if isinstance(structure, list) and len(structure) >= 2 and all(isinstance(x, str) for x in structure):
            times = [
                re.fullmatch(r".+?[（(：:]\s*约?\s*(\d+)\s*分钟\s*[）)]?\s*", item)
                for item in structure
            ]
            if all(match is not None and 0 < int(match.group(1)) <= 240 for match in times):
                allocated = [int(match.group(1)) for match in times]
                design["minutes"] = sum(allocated)
                notes.append(f"minutes <- explicit lecture_plan.structure allocations: {allocated}")

    if target == "response.plan.minutes" and isinstance(o.get("response"), str):
        text = o["response"]
        spans = list(_DURATION.finditer(text))
        allocations = [int(v.group(1)) for v in spans]
        unmatched = []
        for minute in _ALL_MINUTES.finditer(text):
            if any(span.start() <= minute.start() and minute.end() <= span.end() for span in spans):
                continue
            prefix = text[max(0, minute.start() - 12):minute.start()]
            if not re.search(r"(?:在|共|只有|限时|总时长|总计)\s*$", prefix):
                unmatched.append(minute.group())
        if len(allocations) >= 2 and not unmatched and all(0 < minutes <= 240 for minutes in allocations):
            o["response_plan_minutes_observed"] = sum(allocations)
            a["target"] = "response_plan_minutes_observed"
            notes.append(f"plan.minutes <- explicit time allocations: {allocations}")

    return a, o, notes

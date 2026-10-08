from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Protocol

from .models import SemanticAssertionDecision, StructuralAssertionResult

SUPPORTED_ASSERTION_TYPES = {
    "exact_count", "exact_total_score", "score_sum_match", "total_duration_max",
    "duration_min", "duration_max_per_lesson", "coverage_contains", "coverage_excludes",
    "allowed_scope_only", "required_content", "forbidden_content", "source_grounded",
    "reference_used", "constraint_preserved", "count_min_per_topic",
    "allocation_share_max", "required_question_type", "forbidden_question_type",
    "no_duplicate_items", "empty_intersection", "difficulty_distribution",
    "difficulty_order", "monotonic_non_decreasing", "score_distribution",
    "no_fabricated_items", "insufficiency_disclosed", "all_items_match",
    "all_items_match_topic", "topic_count", "group_max_count", "score_share_min",
}


class UnknownAssertionType(ValueError):
    pass


class SemanticEvaluationUnavailable(RuntimeError):
    pass


class SemanticAssertionEvaluator(Protocol):
    def evaluate(
        self, assertion: Mapping[str, Any], actual: Mapping[str, Any], setup: Mapping[str, Any]
    ) -> SemanticAssertionDecision: ...


class UnavailableSemanticEvaluator:
    def evaluate(
        self, assertion: Mapping[str, Any], actual: Mapping[str, Any], setup: Mapping[str, Any]
    ) -> SemanticAssertionDecision:
        raise SemanticEvaluationUnavailable(
            f"semantic evaluator required for assertion {assertion['id']}"
        )


@dataclass
class StructuralAssertionGrader:
    curriculum_mapping: Mapping[str, str] | None = None
    semantic_evaluator: SemanticAssertionEvaluator | None = None

    def grade_all(
        self,
        assertions: Iterable[Mapping[str, Any]],
        actual: Mapping[str, Any],
        setup: Mapping[str, Any],
    ) -> list[StructuralAssertionResult]:
        return [self.grade(assertion, actual, setup) for assertion in assertions]

    def grade(
        self,
        assertion: Mapping[str, Any],
        actual: Mapping[str, Any],
        setup: Mapping[str, Any],
    ) -> StructuralAssertionResult:
        assertion_type = str(assertion["type"])
        if assertion_type not in SUPPORTED_ASSERTION_TYPES:
            raise UnknownAssertionType(assertion_type)
        handler = getattr(self, f"_grade_{assertion_type}", None)
        if handler is None:
            aliases = {
                "monotonic_non_decreasing": self._grade_difficulty_order,
                "topic_count": self._grade_topic_count,
                "all_items_match_topic": self._grade_all_items_match,
            }
            handler = aliases.get(assertion_type)
        if handler is None:
            raise UnknownAssertionType(assertion_type)
        passed, reason, observed = handler(assertion, actual, setup)
        return StructuralAssertionResult(
            assertion_id=str(assertion["id"]),
            type=assertion_type,
            passed=passed,
            critical=bool(assertion.get("critical", False)),
            reason=reason,
            observed=observed,
            expected=assertion.get("expected", assertion.get("forbidden")),
        )

    def _value(self, assertion: Mapping[str, Any], actual: Mapping[str, Any]) -> Any:
        target = assertion.get("target")
        if target == "teaching_design.chapters":
            return sorted(self._derive_chapters(actual))
        if target == "teaching_design.examples":
            return self._collect_named_arrays(actual.get("teaching_design", {}), "examples")
        if target == "teaching_design.misconceptions":
            return self._collect_named_arrays(actual.get("teaching_design", {}), "misconceptions")
        return _resolve(actual, str(target))

    def _semantic(self, assertion, actual, setup):
        evaluator = self.semantic_evaluator or UnavailableSemanticEvaluator()
        decision = evaluator.evaluate(assertion, actual, setup)
        return decision.passed, decision.reason, decision.evidence

    def _grade_exact_count(self, assertion, actual, setup):
        value = self._value(assertion, actual)
        observed = len(value) if isinstance(value, (list, tuple, set, dict)) else value
        expected = assertion["expected"]
        return observed == expected, f"observed count={observed}, expected={expected}", observed

    def _grade_exact_total_score(self, assertion, actual, setup):
        value = self._value(assertion, actual)
        expected = assertion["expected"]
        return value == expected, f"observed total={value}, expected={expected}", value

    def _grade_score_sum_match(self, assertion, actual, setup):
        values = _numbers(self._value(assertion, actual))
        observed = sum(values)
        expected = assertion["expected"]
        return _close(observed, expected), f"item score sum={observed}, expected={expected}", observed

    def _grade_total_duration_max(self, assertion, actual, setup):
        value = self._duration_value(assertion, actual)
        expected = assertion["expected"]
        return value <= expected, f"duration={value}, maximum={expected}", value

    def _grade_duration_min(self, assertion, actual, setup):
        value = self._duration_value(assertion, actual)
        expected = assertion["expected"]
        return value >= expected, f"duration={value}, minimum={expected}", value

    def _grade_duration_max_per_lesson(self, assertion, actual, setup):
        design = actual.get("teaching_design", {})
        durations = [_lesson_minutes(lesson) for lesson in design.get("lessons", [])]
        expected = assertion["expected"]
        passed = bool(durations) and all(value <= expected for value in durations)
        return passed, f"lesson durations={durations}, maximum each={expected}", durations

    def _grade_coverage_contains(self, assertion, actual, setup):
        observed = set(_scalars(self._value(assertion, actual)))
        expected = set(_scalars(assertion["expected"]))
        missing = expected - observed
        return not missing, f"missing coverage={sorted(missing)}", sorted(observed)

    def _grade_coverage_excludes(self, assertion, actual, setup):
        observed = set(_scalars(self._value(assertion, actual)))
        forbidden = set(_scalars(assertion.get("forbidden", [])))
        overlap = observed & forbidden
        return not overlap, f"forbidden coverage={sorted(overlap)}", sorted(observed)

    def _grade_allowed_scope_only(self, assertion, actual, setup):
        observed = set(_scalars(self._value(assertion, actual)))
        allowed = set(_scalars(assertion["expected"]))
        outside = observed - allowed
        return not outside, f"out-of-scope values={sorted(outside)}", sorted(observed)

    def _grade_required_content(self, assertion, actual, setup):
        if assertion.get("match_mode") == "semantic":
            return self._semantic(assertion, actual, setup)
        observed = set(_scalars(self._value(assertion, actual)))
        expected = set(_scalars(assertion["expected"]))
        missing = expected - observed
        return not missing, f"missing required content={sorted(missing)}", sorted(observed)

    def _grade_forbidden_content(self, assertion, actual, setup):
        if assertion.get("match_mode") == "semantic":
            return self._semantic(assertion, actual, setup)
        observed = set(_scalars(self._value(assertion, actual)))
        forbidden = set(_scalars(assertion.get("forbidden", [])))
        overlap = observed & forbidden
        return not overlap, f"forbidden content used={sorted(overlap)}", sorted(observed)

    def _grade_source_grounded(self, assertion, actual, setup):
        return self._semantic(assertion, actual, setup)

    def _grade_reference_used(self, assertion, actual, setup):
        return self._semantic(assertion, actual, setup)

    def _grade_constraint_preserved(self, assertion, actual, setup):
        observed = self._value(assertion, actual)
        expected = assertion["expected"]
        return observed == expected, "constraint value comparison", observed

    def _grade_count_min_per_topic(self, assertion, actual, setup):
        items = self._value(assertion, actual)
        required = _required_topics(setup)
        counts = Counter(
            topic for item in items if isinstance(item, Mapping)
            for topic in _scalars(item.get("topics", item.get("topic", [])))
        )
        minimum = assertion["expected"]
        missing = {topic: counts[topic] for topic in required if counts[topic] < minimum}
        return not missing, f"topics below minimum={missing}", dict(counts)

    def _grade_allocation_share_max(self, assertion, actual, setup):
        chapter = str(assertion["target"]).rsplit(".", 1)[-1]
        shares = self._derive_chapter_time_share(actual)
        observed = shares.get(chapter, 0.0)
        expected = assertion["expected"]
        return observed <= expected, f"{chapter} share={observed:.4f}, maximum={expected}", observed

    def _grade_required_question_type(self, assertion, actual, setup):
        counts = Counter(str(q.get("question_type")) for q in _questions(actual))
        expected = assertion["expected"]
        if isinstance(expected, Mapping):
            passed = all(counts[k] == v for k, v in expected.items())
        else:
            passed = set(_scalars(expected)) <= set(counts)
        return passed, f"question type counts={dict(counts)}", dict(counts)

    def _grade_forbidden_question_type(self, assertion, actual, setup):
        counts = Counter(str(q.get("question_type")) for q in _questions(actual))
        forbidden = set(_scalars(assertion.get("forbidden", [])))
        overlap = set(counts) & forbidden
        return not overlap, f"forbidden question types={sorted(overlap)}", dict(counts)

    def _grade_no_duplicate_items(self, assertion, actual, setup):
        values = _scalars(self._value(assertion, actual))
        duplicates = sorted(k for k, count in Counter(values).items() if count > 1)
        return not duplicates, f"duplicate items={duplicates}", values

    def _grade_empty_intersection(self, assertion, actual, setup):
        observed = set(_scalars(self._value(assertion, actual)))
        excluded = set(_scalars(assertion["expected"]))
        overlap = observed & excluded
        return not overlap, f"unexpected intersection={sorted(overlap)}", sorted(observed)

    def _grade_difficulty_distribution(self, assertion, actual, setup):
        questions = _questions(actual)
        totals = defaultdict(float)
        for question in questions:
            totals[str(question.get("difficulty"))] += float(question.get("score", 0))
        total = sum(totals.values())
        observed = {key: value / total for key, value in totals.items()} if total else {}
        tolerance = float(assertion.get("tolerance", 0))
        expected = assertion["expected"]
        passed = total > 0 and all(abs(observed.get(k, 0) - v) <= tolerance for k, v in expected.items())
        return passed, f"difficulty score shares={observed}", observed

    def _grade_difficulty_order(self, assertion, actual, setup):
        values = _scalars(self._value(assertion, actual))
        order = assertion.get("expected_rank") or {
            value: index for index, value in enumerate(assertion.get("expected", []))
        }
        try:
            ranks = [order[value] for value in values]
        except KeyError as exc:
            return False, f"unknown difficulty={exc.args[0]}", values
        passed = all(left <= right for left, right in pairwise(ranks))
        return passed, f"difficulty ranks={ranks}", values

    def _grade_score_distribution(self, assertion, actual, setup):
        by_type = defaultdict(float)
        for question in _questions(actual):
            by_type[str(question.get("question_type"))] += float(question.get("score", 0))
        observed: dict[str, float] = {}
        for label in assertion["expected"]:
            types = label.replace("题", "题").split("和")
            observed[label] = sum(by_type[t] for t in types)
        expected = assertion["expected"]
        passed = all(_close(observed[k], value) for k, value in expected.items())
        return passed, f"score distribution={observed}", observed

    def _grade_no_fabricated_items(self, assertion, actual, setup):
        available = setup.get("available_item_ids")
        if available is None:
            raise ValueError("no_fabricated_items requires setup.available_item_ids")
        selected = {str(q.get("id")) for q in _questions(actual)}
        fabricated = selected - set(map(str, available))
        return not fabricated, f"fabricated item ids={sorted(fabricated)}", sorted(selected)

    def _grade_insufficiency_disclosed(self, assertion, actual, setup):
        return self._semantic(assertion, actual, setup)

    def _grade_all_items_match(self, assertion, actual, setup):
        expected = assertion["expected"]
        values = [set(_scalars(q.get("topics", q.get("topic", [])))) for q in _questions(actual)]
        passed = bool(values) and all(expected in topics for topics in values)
        return passed, f"item topic sets={values}", [sorted(value) for value in values]

    def _grade_topic_count(self, assertion, actual, setup):
        expected_topic = assertion["expected"]["topic"]
        expected_count = assertion["expected"]["count"]
        observed = sum(
            expected_topic in set(_scalars(q.get("topics", q.get("topic", []))))
            for q in _questions(actual)
        )
        return observed == expected_count, f"topic count={observed}", observed

    def _grade_group_max_count(self, assertion, actual, setup):
        counts = Counter(str(q.get("similarity_group")) for q in _questions(actual) if q.get("similarity_group"))
        maximum = assertion["expected"]
        offenders = {group: count for group, count in counts.items() if count > maximum}
        return not offenders, f"groups over maximum={offenders}", dict(counts)

    def _grade_score_share_min(self, assertion, actual, setup):
        topic = str(assertion["target"]).rsplit(".", 1)[-1]
        questions = _questions(actual)
        total = sum(float(q.get("score", 0)) for q in questions)
        matching = sum(
            float(q.get("score", 0)) for q in questions
            if topic in set(_scalars(q.get("topics", q.get("topic", []))))
        )
        observed = matching / total if total else 0.0
        expected = assertion["expected"]
        return observed >= expected, f"{topic} score share={observed:.4f}, minimum={expected}", observed

    def _duration_value(self, assertion, actual):
        try:
            value = self._value(assertion, actual)
        except KeyError:
            design = actual.get("teaching_design", {})
            lessons = design.get("lessons", [])
            if lessons:
                return sum(_lesson_minutes(lesson) for lesson in lessons)
            raise
        if isinstance(value, list):
            return sum(_numbers(value))
        return float(value)

    def _derive_chapters(self, actual: Mapping[str, Any]) -> set[str]:
        mapping = dict(self.curriculum_mapping or {})
        design = actual.get("teaching_design", {})
        topics = _collect_topic_values(design)
        chapters: set[str] = set()
        known_chapters = set(mapping.values())
        for topic in topics:
            if topic in mapping:
                chapters.add(mapping[topic])
            elif topic in known_chapters:
                chapters.add(topic)
            else:
                raise ValueError(f"curriculum mapping missing topic: {topic}")
        return chapters

    def _derive_chapter_time_share(self, actual: Mapping[str, Any]) -> dict[str, float]:
        mapping = dict(self.curriculum_mapping or {})
        minutes = defaultdict(float)
        design = actual.get("teaching_design", {})
        for lesson in design.get("lessons", []):
            for activity in lesson.get("activities", []):
                topics = _scalars(activity.get("topics", activity.get("topic", [])))
                chapters = {mapping.get(topic, topic if topic in mapping.values() else None) for topic in topics}
                if None in chapters or len(chapters) != 1:
                    raise ValueError("each timed activity must map to exactly one curriculum chapter")
                minutes[chapters.pop()] += float(activity.get("minutes", 0))
        total = sum(minutes.values())
        if total <= 0:
            raise ValueError("chapter time share requires timed activities")
        return {chapter: value / total for chapter, value in minutes.items()}

    @staticmethod
    def _collect_named_arrays(value: Any, name: str) -> list[Any]:
        result: list[Any] = []
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key == name and isinstance(item, list):
                    result.extend(item)
                else:
                    result.extend(StructuralAssertionGrader._collect_named_arrays(item, name))
        elif isinstance(value, list):
            for item in value:
                result.extend(StructuralAssertionGrader._collect_named_arrays(item, name))
        return result


def _resolve(value: Any, path: str) -> Any:
    parts = path.split(".")
    if parts and parts[0] not in value and parts[0] in {"response", "paper", "teaching_design"}:
        raise KeyError(path)
    return _resolve_parts(value, parts)


def _resolve_parts(value: Any, parts: list[str]) -> Any:
    if not parts:
        return value
    if isinstance(value, list):
        result = []
        for item in value:
            resolved = _resolve_parts(item, parts)
            result.extend(resolved if isinstance(resolved, list) else [resolved])
        return result
    if not isinstance(value, Mapping) or parts[0] not in value:
        raise KeyError(".".join(parts))
    return _resolve_parts(value[parts[0]], parts[1:])


def _scalars(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [item for child in value.values() for item in _scalars(child)]
    if isinstance(value, (list, tuple, set)):
        return [item for child in value for item in _scalars(child)]
    return [value]


def _numbers(value: Any) -> list[float]:
    return [float(item) for item in _scalars(value)]


def _close(left: float, right: float, tolerance: float = 1e-9) -> bool:
    return abs(left - right) <= tolerance


def _questions(actual: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(actual.get("paper", {}).get("questions", []))


def _lesson_minutes(lesson: Mapping[str, Any]) -> float:
    if "minutes" in lesson:
        return float(lesson["minutes"])
    return sum(float(activity.get("minutes", 0)) for activity in lesson.get("activities", []))


def _required_topics(setup: Mapping[str, Any]) -> set[str]:
    topics = setup.get("required_topics")
    if topics is None:
        raise ValueError("count_min_per_topic requires setup.required_topics")
    return set(_scalars(topics))


def _collect_topic_values(value: Any, *, parent: str | None = None) -> set[str]:
    topics: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"topic", "topics", "knowledge_points", "scope_names"} or key == "name" and parent == "knowledge_plan":
                topics.update(map(str, _scalars(item)))
            else:
                topics.update(_collect_topic_values(item, parent=key))
    elif isinstance(value, list):
        for item in value:
            topics.update(_collect_topic_values(item, parent=parent))
    return topics

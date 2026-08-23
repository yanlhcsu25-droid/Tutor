"""统一 OCR 文档导入编排。

布局差异只在本模块中被消化；输出一律是 QuestionCandidate，Draft 以后不分模式。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence, TypeVar

from .ocr import (
    PlacedCandidate,
    QuestionCandidate,
    RawQuestion,
    _is_page_furniture,
    _should_join_cross_page,
    split_candidate_subquestions,
    split_major_questions,
    split_pages_into_candidates,
)


@dataclass(frozen=True)
class DocumentLayout:
    solution_mode: Literal["inline", "separate"] = "inline"
    question_pages: list[int] = field(default_factory=list)
    solution_pages: list[int] = field(default_factory=list)

    def validate(self, available_pages: set[int]) -> None:
        # separate 模式允许两个页表都为空：表示稍后依据 OCR Markdown 自动识别答案区。
        questions = set(self.question_pages) if self.question_pages else available_pages
        solutions = set(self.solution_pages)
        missing = (questions | solutions) - available_pages
        if missing:
            raise ValueError(f"页码超出 PDF 范围：{sorted(missing)}")
        if self.solution_mode == "separate":
            if self.question_pages and self.solution_pages:
                if questions & solutions:
                    raise ValueError("题目页和答案页不能重叠")
            elif self.question_pages or self.solution_pages:
                raise ValueError("自动识别模式不能只指定一类页码")

    def to_dict(self) -> dict[str, object]:
        return {
            "solution_mode": self.solution_mode,
            "question_pages": list(self.question_pages),
            "solution_pages": list(self.solution_pages),
        }

    @classmethod
    def from_dict(
        cls, value: dict[str, object] | None, *, available_pages: list[int] | None = None
    ) -> "DocumentLayout":
        if not value:
            # 明确的 legacy fallback：布局字段上线前的 source 均为普通习题。
            return cls("inline", list(available_pages or []), [])
        mode = value.get("solution_mode", "inline")
        if mode not in {"inline", "separate"}:
            raise ValueError(f"无效的 source 导入布局：{mode}")
        return cls(
            mode,  # type: ignore[arg-type]
            [int(item) for item in value.get("question_pages", [])],  # type: ignore[union-attr]
            [int(item) for item in value.get("solution_pages", [])],  # type: ignore[union-attr]
        )


@dataclass
class QuestionStub:
    key: tuple[str | None, str]
    candidate: QuestionCandidate
    page_number: int
    bbox: dict[str, float] | None = None


@dataclass
class SolutionCandidate:
    key: tuple[str | None, str]
    answer: str
    analysis: str
    page_number: int


@dataclass
class ImportDiagnostics:
    unmatched_solutions: list[SolutionCandidate] = field(default_factory=list)
    ambiguous_keys: list[str] = field(default_factory=list)
    # 题目存在但对应答案缺失，不进入正常待审核，留待人工补全（不删除）。
    missing_questions: list[QuestionStub] = field(default_factory=list)


@dataclass
class ImportResult:
    candidates: list[PlacedCandidate]
    diagnostics: ImportDiagnostics = field(default_factory=ImportDiagnostics)


def normalize_match_number(number: str) -> str:
    value = number.strip()
    value = re.sub(r"^第\s*(\d+)\s*题\s*第\s*(\d+)\s*问$", r"\1-\2", value)
    value = value.replace("第", "").replace("题", "")
    value = value.replace("（", "(").replace("）", ")").replace("．", ".")
    value = re.sub(r"[、.]$", "", re.sub(r"\s+", "", value))
    # 3.(1), 3（1）, 3-1 and 第3题第1问 share one deterministic key.
    value = re.sub(r"^(\d+)[.\-]?\((\d+)\)$", r"\1-\2", value)
    value = re.sub(r"^(\d+)[.\-]?（(\d+)）$", r"\1-\2", value)
    value = re.sub(r"^(\d+)第(\d+)问$", r"\1-\2", value)
    return value


def _key(section: str | None, number: str) -> tuple[str | None, str]:
    return section, normalize_match_number(number)


_SectionItem = TypeVar("_SectionItem")
_IMPLICIT_SECTION = "__implicit__"


def _top_level_number(number: str) -> int | None:
    """Return only a top-level integer; child keys such as ``8(1)`` are excluded."""
    normalized = normalize_match_number(number)
    return int(normalized) if normalized.isdigit() else None


def _has_confirmed_restart(
    numbers: Sequence[int | None],
    explicit_sections: Sequence[str | None],
    index: int,
) -> bool:
    """Require two following top-level numbers to confirm an implicit new group."""
    current = numbers[index]
    if current is None or explicit_sections[index] is not None:
        return False
    expected = current + 1
    confirmed = 0
    for following in range(index + 1, len(numbers)):
        if explicit_sections[following] is not None:
            break
        number = numbers[following]
        if number is None:
            continue
        if number != expected:
            return False
        confirmed += 1
        if confirmed == 2:
            return True
        expected += 1
    return False


def _recover_section_occurrences(
    items: Sequence[_SectionItem],
    *,
    get_section: Callable[[_SectionItem], str | None],
    get_number: Callable[[_SectionItem], str],
    set_section: Callable[[_SectionItem, str | None], None],
) -> None:
    """Apply one occurrence-recovery rule to question and solution top-level blocks."""
    if not items:
        return
    explicit_sections = [get_section(item) for item in items]
    numbers = [_top_level_number(get_number(item)) for item in items]
    restart_indexes: set[int] = set()
    previous_number: int | None = None
    for index, number in enumerate(numbers):
        if number is None:
            continue
        if (
            previous_number is not None
            and number < previous_number
            and _has_confirmed_restart(numbers, explicit_sections, index)
        ):
            restart_indexes.add(index)
        previous_number = number

    first_explicit = next(
        (index for index, section in enumerate(explicit_sections) if section is not None),
        len(items),
    )
    initial_implicit = any(index < first_explicit for index in restart_indexes)
    counts: dict[str, int] = {}
    active_base: str | None = None
    active_key: str | None = None
    for index, item in enumerate(items):
        base = explicit_sections[index]
        if active_key is None and base is None and initial_implicit:
            active_base = _IMPLICIT_SECTION
            counts[active_base] = 1
            active_key = f"{active_base}#1"
        if index in restart_indexes:
            active_base = active_base or _IMPLICIT_SECTION
            counts[active_base] = counts.get(active_base, 0) + 1
            active_key = f"{active_base}#{counts[active_base]}"
        if base is not None and base != active_base:
            counts[base] = counts.get(base, 0) + 1
            active_base = base
            active_key = f"{base}#{counts[base]}"
        elif base is None and active_key is None:
            set_section(item, None)
            continue
        set_section(item, active_key)


def _qualify_repeated_sections(items: Sequence[QuestionStub]) -> None:
    """Recover explicit and implicit occurrences for parsed question blocks."""
    def set_question_section(item: QuestionStub, section: str | None) -> None:
        item.candidate.section_key = section
        item.key = _key(section, item.candidate.original_number)

    _recover_section_occurrences(
        items,
        get_section=lambda item: item.candidate.section_key,
        get_number=lambda item: item.candidate.original_number,
        set_section=set_question_section,
    )


def _qualify_repeated_solution_sections(items: Sequence[SolutionCandidate]) -> None:
    """Use the same occurrence recovery used by question blocks."""
    _recover_section_occurrences(
        items,
        get_section=lambda item: item.key[0],
        get_number=lambda item: item.key[1],
        set_section=lambda item, section: setattr(
            item, "key", _key(section, item.key[1])
        ),
    )


def extract_questions(pages: Sequence[tuple[int, str]]) -> list[QuestionStub]:
    stubs: list[QuestionStub] = []
    for placed in split_pages_into_candidates(pages):
        for candidate in split_candidate_subquestions(placed.candidate):
            stubs.append(QuestionStub(
                key=_key(candidate.section_key, candidate.original_number),
                candidate=candidate,
                page_number=placed.page_number,
                bbox=placed.bbox if candidate.original_number == placed.candidate.original_number else None,
            ))
    _qualify_repeated_sections(stubs)
    return stubs


_SOLUTION_PREFIX_RE = re.compile(
    r"^\s*(?:(?:参考)?答案|解析|解答)\s*[:：]?\s*|^\s*(?:解|证)\s*[:：]?\s*"
)
_SUB_SOLUTION_RE = re.compile(r"(?m)^\s*(?:解|解析)?\s*[:：]?\s*[(（]\s*(\d+)\s*[)）]\s*")
_CHINESE_MAJOR_SOLUTION_RE = re.compile(
    r"(?m)^\s*(?:#{1,4}\s*)?第\s*(\d+)\s*题\s*[:：]?\s*"
)


def _solution_parts(text: str) -> tuple[str, str]:
    text = text.strip()
    # 套卷中的“解/证”通常承载完整过程，作为 analysis 保存；显式答案则作为 answer。
    if re.match(r"^\s*(?:解|证|解析|解答)", text):
        return "", _SOLUTION_PREFIX_RE.sub("", text, count=1).strip()
    return _SOLUTION_PREFIX_RE.sub("", text, count=1).strip(), ""


def extract_solutions(pages: Sequence[tuple[int, str]]) -> list[SolutionCandidate]:
    output: list[SolutionCandidate] = []

    # separate 答案页常用“第N题”。一旦检测到该风格，优先按它切大题；
    # 正文中的“1. 因为……”只能留在该答案正文中，不能抢占大题边界。
    if any(_CHINESE_MAJOR_SOLUTION_RE.search(markdown) for _, markdown in pages):
        for page_number, markdown in pages:
            matches = list(_CHINESE_MAJOR_SOLUTION_RE.finditer(markdown))
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
                answer, analysis = _solution_parts(markdown[match.end():end])
                output.append(SolutionCandidate(
                    _key(None, match.group(1)), answer, analysis, page_number
                ))
        _qualify_repeated_solution_sections(output)
        return output

    pending: tuple[int, RawQuestion] | None = None

    def promote_expected_cross_page_boundary(
        preamble: str,
        chunks: list[RawQuestion],
    ) -> tuple[str, list[RawQuestion]]:
        """识别续页中单独出现的下一个 ``(N)`` 答案边界。

        通用切题器为避免把公式编号当大题，要求无标题页面至少出现两个连续
        括号题号；但答案跨页时常只有一个 ``(15)B.``。此处已知上一题号，
        只提升严格等于 ``上一题+1`` 的行首编号，因此不会把任意公式编号
        当作答案边界。
        """
        if pending is None:
            return preamble, chunks
        previous = normalize_match_number(pending[1].original_number)
        if not previous.isdigit():
            return preamble, chunks
        expected = int(previous) + 1
        marker = re.search(
            rf"(?m)^[ \t]*[（(][ \t]*{expected}[ \t]*[)）][ \t]*(?=\S)",
            preamble,
        )
        if marker is None:
            return preamble, chunks
        promoted = RawQuestion(
            original_number=str(expected),
            raw=preamble[marker.end():].strip(),
            question_type=pending[1].question_type,
            section_key=pending[1].section_key,
        )
        return preamble[:marker.start()].strip(), [promoted, *chunks]

    def append_chunk(page_number: int, chunk: RawQuestion) -> None:
        matches = list(_SUB_SOLUTION_RE.finditer(chunk.raw))
        if len(matches) >= 2:
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(chunk.raw)
                answer, analysis = _solution_parts(chunk.raw[match.end():end])
                number = f"{chunk.original_number}({match.group(1)})"
                output.append(SolutionCandidate(_key(chunk.section_key, number), answer, analysis, page_number))
        else:
            answer, analysis = _solution_parts(chunk.raw)
            output.append(SolutionCandidate(
                _key(chunk.section_key, chunk.original_number), answer, analysis, page_number
            ))

    for page_number, markdown in pages:
        preamble, chunks = split_major_questions(markdown)
        preamble, chunks = promote_expected_cross_page_boundary(preamble, chunks)
        # Some answer pages omit the number for the first answer and begin
        # directly with its explanation, while later answers use ``2.D`` /
        # ``3.B``.  If the first explicit answer number is greater than one,
        # conservatively assign the non-furniture preamble to the preceding
        # question instead of dropping it as an unmatched page fragment.
        if pending is None and chunks and preamble and int(normalize_match_number(chunks[0].original_number).split("-")[0]) > 1:
            if not _is_page_furniture(preamble):
                inferred_number = str(
                    int(normalize_match_number(chunks[0].original_number).split("-")[0]) - 1
                )
                answer, analysis = _solution_parts(preamble)
                output.append(SolutionCandidate(
                    _key(None, inferred_number), answer, analysis, page_number
                ))
            preamble = ""
        if pending is not None and preamble and _should_join_cross_page(pending[1].raw, preamble):
            # 当前页首续写内容属于上一页 pending 答案；家具文本（页眉/章节标题）则不拼。
            pending[1].raw = f"{pending[1].raw}\n\n{preamble}".strip()
        elif pending is None and preamble and not _is_page_furniture(preamble):
            # 无上题可归属的答案正文也必须进入 unmatched diagnostics，不能静默丢弃；
            # 但页眉/页脚等家具文本直接丢弃，不污染 unmatched。
            answer, analysis = _solution_parts(preamble)
            output.append(SolutionCandidate((None, f"__page_{page_number}"), answer, analysis, page_number))
        if not chunks:
            continue
        if pending is not None:
            append_chunk(*pending)
        for chunk in chunks[:-1]:
            append_chunk(page_number, chunk)
        pending = (page_number, chunks[-1])
    if pending is not None:
        append_chunk(*pending)
    _qualify_repeated_solution_sections(output)
    return output


def match_questions_and_solutions(
    questions: Sequence[QuestionStub], solutions: Sequence[SolutionCandidate]
) -> ImportResult:
    q_by_key: dict[tuple[str | None, str], list[QuestionStub]] = {}
    s_by_key: dict[tuple[str | None, str], list[SolutionCandidate]] = {}
    for item in questions:
        q_by_key.setdefault(item.key, []).append(item)
    for item in solutions:
        s_by_key.setdefault(item.key, []).append(item)

    diagnostics = ImportDiagnostics()
    output: list[PlacedCandidate] = []
    all_keys = set(q_by_key) | set(s_by_key)
    # 题目页常有章节标题（section_key），答案页通常没有。为 separate
    # 套卷准备一个安全兜底：仅当标准化题号在两侧都唯一时允许跨 section 匹配。
    q_by_number: dict[str, list[QuestionStub]] = {}
    s_by_number: dict[str, list[SolutionCandidate]] = {}
    for item in questions:
        q_by_number.setdefault(normalize_match_number(item.candidate.original_number), []).append(item)
    for item in solutions:
        s_by_number.setdefault(normalize_match_number(item.key[1]), []).append(item)
    ambiguous = {key for key in all_keys if len(q_by_key.get(key, [])) != 1 or len(s_by_key.get(key, [])) > 1}
    diagnostics.ambiguous_keys = [f"{section or '-'}:{number}" for section, number in sorted(ambiguous, key=str)]

    unmatched_questions: list[QuestionStub] = []
    for question in questions:
        matches = s_by_key.get(question.key, [])
        match_method = "exact_number"
        # section_key 不一致时，仅使用唯一题号兜底；绝不按位置 zip。
        if not matches:
            number = normalize_match_number(question.candidate.original_number)
            number_matches = s_by_number.get(number, [])
            if len(q_by_number.get(number, [])) == 1 and len(number_matches) == 1:
                matches = number_matches
                match_method = "number_only"
        candidate = question.candidate
        if len(q_by_number.get(normalize_match_number(question.candidate.original_number), [])) == 1 and len(matches) == 1:
            candidate.answer = matches[0].answer
            candidate.analysis = matches[0].analysis
            candidate.match_method = match_method
            candidate.matched = True
            candidate.match_status = "matched"
            candidate.answer_page = matches[0].page_number
        else:
            unmatched_questions.append(question)
        output.append(PlacedCandidate(question.page_number, candidate, question.bbox))

    # 删除危险的"顺序强行配对"：只要题号不同，禁止按位置 zip 配。
    # 未匹配题按"无对应答案 / 题号歧义"分类，全部进入待人工核对，不进入正常待审核。
    for question in unmatched_questions:
        candidate = question.candidate
        candidate.needs_review = True
        candidate.matched = False
        candidate.match_method = "unmatched"
        if question.key not in s_by_key:
            candidate.match_status = "missing_answer"
            candidate.review_note = "answer_not_found（未找到与该题号对应的参考解答）"
            diagnostics.missing_questions.append(question)
        else:
            candidate.match_status = "ambiguous"
            candidate.review_note = "题号重复或参考解答归属存在歧义，未自动匹配，请人工核对。"

    for solution in solutions:
        exact_question_matches = q_by_key.get(solution.key, [])
        number = normalize_match_number(solution.key[1])
        number_question_matches = q_by_number.get(number, [])
        if (
            (len(exact_question_matches) != 1 or len(s_by_key[solution.key]) != 1)
            and not (len(number_question_matches) == 1 and len(s_by_number.get(number, [])) == 1)
        ):
            diagnostics.unmatched_solutions.append(solution)
    return ImportResult(output, diagnostics)


_ANSWER_SECTION_RE = re.compile(
    r"(?im)^\s*(?:#{1,4}\s*)?(?:参考答案|参考解答|答案与解析|答案解析|试题答案|习题答案|答案|解析|解答)\s*[:：]?\s*$"
)
_ANSWER_PAGE_HINT_RE = re.compile(
    r"(?im)(?:^\s*第\s*\d+\s*题\s*[:：]?|^\s*(?:解|证|解析)\s*[:：])"
)


def infer_separate_layout(pages: Sequence[tuple[int, str]]) -> DocumentLayout:
    """从整份 OCR Markdown 推断题目页/答案页边界。

    这是保守识别：优先使用“参考答案/答案与解析”等明确标题；没有标题时，
    只接受同时具备答案页题号和“解/证/解析”特征的页面，避免把题目中的“答案”
    误当成答案区。识别失败会明确报错，绝不按顺序强行配对。
    """
    ordered = sorted(pages, key=lambda item: item[0])
    if len(ordered) < 2:
        raise ValueError("自动识别答案区至少需要题目页和答案页")
    scores: list[tuple[int, int]] = []
    for number, markdown in ordered:
        score = 0
        if _ANSWER_SECTION_RE.search(markdown):
            score += 5
        if _ANSWER_PAGE_HINT_RE.search(markdown):
            score += 2
        # 答案页通常没有连续的题干式一级题号，且含多个“第N题”。
        if len(re.findall(r"(?m)^\s*第\s*\d+\s*题", markdown)) >= 2:
            score += 2
        scores.append((number, score))
    starts = [number for number, score in scores if score >= 5]
    if not starts:
        starts = [number for number, score in scores if score >= 4]
    if not starts:
        raise ValueError("未能根据 OCR Markdown 识别答案区，请确认答案页包含“参考答案/解析”或“第N题 解”标记")
    solution_start = min(starts)
    question_pages = [number for number, _ in ordered if number < solution_start]
    solution_pages = [number for number, _ in ordered if number >= solution_start]
    if not question_pages or not solution_pages:
        raise ValueError(f"自动识别到答案区起始页为第{solution_start}页，但题目页或答案页为空")
    return DocumentLayout("separate", question_pages, solution_pages)


def import_document(pages: Sequence[tuple[int, str]], layout: DocumentLayout) -> ImportResult:
    available = {number for number, _ in pages}
    layout.validate(available)
    by_number = dict(pages)
    if layout.solution_mode == "inline":
        question_numbers = layout.question_pages or sorted(available)
        return ImportResult(split_pages_into_candidates(
            [(number, by_number[number]) for number in question_numbers]
        ))
    effective_layout = layout
    if not layout.question_pages and not layout.solution_pages:
        effective_layout = infer_separate_layout(pages)
    questions = extract_questions([(number, by_number[number]) for number in effective_layout.question_pages])
    solutions = extract_solutions([(number, by_number[number]) for number in effective_layout.solution_pages])
    return match_questions_and_solutions(questions, solutions)

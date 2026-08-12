"""统一 OCR 文档导入编排。

布局差异只在本模块中被消化；输出一律是 QuestionCandidate，Draft 以后不分模式。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Sequence

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
        questions = set(self.question_pages) if self.question_pages else available_pages
        solutions = set(self.solution_pages)
        missing = (questions | solutions) - available_pages
        if missing:
            raise ValueError(f"页码超出 PDF 范围：{sorted(missing)}")
        if self.solution_mode == "separate":
            if not questions or not solutions:
                raise ValueError("套卷模式必须同时指定题目页和答案页")
            if questions & solutions:
                raise ValueError("题目页和答案页不能重叠")

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
        return output

    pending: tuple[int, RawQuestion] | None = None

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
    ambiguous = {key for key in all_keys if len(q_by_key.get(key, [])) != 1 or len(s_by_key.get(key, [])) > 1}
    diagnostics.ambiguous_keys = [f"{section or '-'}:{number}" for section, number in sorted(ambiguous, key=str)]

    unmatched_questions: list[QuestionStub] = []
    for question in questions:
        matches = s_by_key.get(question.key, [])
        candidate = question.candidate
        if len(q_by_key[question.key]) == 1 and len(matches) == 1:
            candidate.answer = matches[0].answer
            candidate.analysis = matches[0].analysis
            candidate.match_method = "exact_number"
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
        if len(q_by_key.get(solution.key, [])) != 1 or len(s_by_key[solution.key]) != 1:
            diagnostics.unmatched_solutions.append(solution)
    return ImportResult(output, diagnostics)


def import_document(pages: Sequence[tuple[int, str]], layout: DocumentLayout) -> ImportResult:
    available = {number for number, _ in pages}
    layout.validate(available)
    by_number = dict(pages)
    question_numbers = layout.question_pages or sorted(available)
    question_pages = [(number, by_number[number]) for number in question_numbers]
    if layout.solution_mode == "inline":
        return ImportResult(split_pages_into_candidates(question_pages))
    questions = extract_questions(question_pages)
    solutions = extract_solutions([(number, by_number[number]) for number in layout.solution_pages])
    return match_questions_and_solutions(questions, solutions)

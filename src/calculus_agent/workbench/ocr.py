from __future__ import annotations

import json
import re
import shutil
import uuid
import gc
import tempfile
import time
import os
import subprocess
import sys
import pypdfium2 as pdfium
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import Any, Iterable

from .database import WorkbenchDatabase
from .markdown_schema import fixed_template
from .question_type_classifier import OPTION_TOKEN_RE, extract_normalized_options, infer_question_type
from calculus_agent.ocr.pdf_preprocess import prepare_pdf_for_ocr


def _rss_mb() -> float:
    """Best-effort current RSS; avoids making psutil a hard dependency."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        import resource
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes; Linux reports KiB.
        return raw / (1024 * 1024 if sys.platform == "darwin" else 1024)


SECTION_HEADING_RE = re.compile(
    r"(?m)^#{1,4}\s*([一二三四五六七八九十]+)[、.．]\s*([^\n]+)$"
)

# 标准题号：7. / 7． / 7、 / 1.2.
QUESTION_START_RE = re.compile(
    r"(?m)^[ \t]*(\d{1,3}(?:[.．]\d+)*)[、.．][ \t]*(?=\S)"
)

# PaddleOCR 对教材题号前的小图标容易产生类似：
#   $ 离 ^{*}7$ .当……
#   $ 当 ^{*}8.$ 当……
#   $ 灌 ^{*}10.$ .证明……
# 这里仅在“行首很短的噪声 + 整数题号 + 句点/顿号”形态下修复，
# 并要求句点后不是数字，避免把 0.001 之类的小数误识别为题号。
MALFORMED_QUESTION_LINE_RE = re.compile(
    r"^(?P<prefix>[^\n]{0,18}?)(?<!\d)"
    r"(?P<number>\d{1,3})"
    r"(?P<between>[ \t\$}\]]*)"
    r"[、.．](?!\d)"
    r"(?P<rest>.*)$"
)

# 一级大题：噪声前缀 + 整数题号 +（明确分隔符 或 紧跟题干关键词）。
# 用于修复 OCR 把一级大题题号包进 $…$ 且没有分隔符的情况，例如：
#   "$ \*3$ 根据函数极限的定义……"  ->  3.根据函数极限的定义……
# 设计约束（对应“不要过宽”的要求）：
#   A. 行首只允许少量“非字母、非括号”的 OCR/符号噪声；
#   B. 题号必须是整数 1~999（不允许小数点，避免把 3.5 当题号）；
#   C. 题号前不能是数字或负号（排除 -4.5）；题号后要么有分隔符 [、.．]（且后接非数字，
#      排除 3.5 小数），要么没有分隔符但紧跟题干关键词；
#   D. 无分隔符时，题号后必须紧跟题干关键词（求/计算/证明/利用/设/已知/判断/讨论/根据）；
#   E. 其后不能是 ) / ）（排除 (3) 这类子题编号）。
# 该正则只在 _normalize_question_line 中对“含 OCR 噪声前缀”的行触发，不会把普通文本误判。
NOISE_QUESTION_RE = re.compile(
    r"^(?P<noise>[ \t$*^{}\\[\]■▣●▪·—–・]{0,12})"
    r"(?<![\d\-−])"
    r"(?P<number>\d{1,3})"
    r"(?:"
    r"(?P<sep>[、.．])(?!\d)"
    r"|(?=[ \t$\\}］\]]*(?:求|计算|证明|利用|设|已知|判断|讨论|根据))"
    r")"
    r"(?P<rest>(?!\)|）)[^\n]*)$"
)

ANSWER_MARKER_RE = re.compile(
    r"(?m)^[ \t]*(?:答案|参考答案)[ \t]*[:：][ \t]*"
)

# 兼容：解析： / 解答： / 解 / 证 / 解由于 / 解因为 / 证(1) / 证（1）
# 同时允许行首有少量 OCR 垃圾（如“EM 证因为”）。
ANALYSIS_MARKER_RE = re.compile(
    r"(?m)(?:^[ \t]*|(?<=[。！？.!?])[ \t]*)(?:[A-Z]{1,5}[ \t]+)?(?:"
    r"(?:解析|解答|答案解析)[ \t]*[:：][ \t]*"
    r"|(?:解|证)(?=(?:[ \t:：（(]|因为|由于|由|令|设|根据|$))[ \t]*[:：]?[ \t]*"
    r")"
)

# 只删除非常明确的广告/页脚噪声；不做泛化文本清洗，避免误删题目正文。
NOISE_LINE_RE = re.compile(
    r"(?:微信公众号|获取更多考研资源|配套课程请加QQ群|QQ群[:：]?\s*\d{5,})"
)

# PPStructure 会把页面中的图片写成 HTML / Markdown 图片标签。
# 当前 MVP 暂不处理图片本身，统一替换成醒目的人工核对占位符，
# 避免实时预览直接显示 <div><img ...> 这类源码。
IMAGE_PLACEHOLDER = "[图片内容暂未解析，请人工核对原PDF]"
IMAGE_LINE_RE = re.compile(
    r"(?:<img\b[^>]*>|!\[[^\]\n]*\]\([^\n)]*\))",
    re.IGNORECASE,
)


class OCRPipelineError(RuntimeError):
    """OCR/题目切分失败，同时保留已经成功解析的页数。"""

    def __init__(self, message: str, *, page_count: int = 0) -> None:
        super().__init__(message)
        self.page_count = page_count


@dataclass
class QuestionCandidate:
    original_number: str
    question_type: str
    body: str
    options: dict[str, str]
    answer: str
    analysis: str
    # 子题拆分时若参考解答无法按题号可靠对应，标记需人工核对。
    needs_review: bool = False
    review_note: str = ""
    # 仅供导入阶段做保守匹配；Draft 及后续业务不依赖来源布局。
    section_key: str | None = None
    match_method: str = "inline"
    matched: bool = True
    match_status: str = "matched"
    answer_page: int | None = None


@dataclass
class RawQuestion:
    original_number: str
    raw: str
    question_type: str
    section_key: str | None = None


@dataclass
class PendingQuestion:
    original_number: str
    raw: str
    question_type: str
    page_number: int
    bbox: dict[str, float] | None
    section_key: str | None = None


@dataclass
class PlacedCandidate:
    """跨页合并之后、已经确定归属页的候选大题。"""

    page_number: int
    candidate: QuestionCandidate
    bbox: dict[str, float] | None = None


@dataclass
class RenderedDraft:
    """候选题渲染成八段式模板之后的入库单元（可能是拆分后的子题）。"""

    page_number: int
    original_number: str
    markdown: str
    bbox: dict[str, float] | None = None
    match_status: str = "matched"
    match_method: str = "inline"
    review_note: str = ""


@dataclass
class SplitTrace:
    """切题过程的可序列化摘要，不包含完整 OCR 正文。"""

    pages: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    warnings: list[str]


# 给定页码与该页的独立候选，返回等长的 bbox 列表。
BboxProvider = Callable[[int, list[QuestionCandidate]], list[dict[str, float] | None]]


# ---------- 文本清洗 / 题号规范化 ----------

def _clean_ocr_markdown(markdown: str) -> str:
    """清理广告与图片标签，并修复教材中畸形题号。

    原始 OCR Markdown 文件仍完整保存在 ocr_raw 中；这里的清洗只影响
    后续题目切分和人工审核模板，因此不会丢失原始 OCR 结果。
    """
    cleaned_lines: list[str] = []
    for line in markdown.splitlines():
        if NOISE_LINE_RE.search(line):
            continue

        # 当前阶段不渲染 OCR 裁出的图片：只要这一行包含图片标签，
        # 就用统一占位符替代整行。后续如果要支持图片题，再单独接图片资源。
        if IMAGE_LINE_RE.search(line):
            if not cleaned_lines or cleaned_lines[-1] != IMAGE_PLACEHOLDER:
                cleaned_lines.append(IMAGE_PLACEHOLDER)
            continue

        cleaned_lines.append(_normalize_question_line(line))
    return "\n".join(cleaned_lines).strip()


def normalize_page(markdown: str) -> str:
    """阶段 1：规范化单页 OCR，供导入和调试共用。"""
    return _clean_ocr_markdown(markdown)


def _normalize_question_line(line: str) -> str:
    # 已经是标准题号则不动。
    if QUESTION_START_RE.match(line):
        return line

    # 退化路径 1：噪声前缀 + 整数题号 + 明确分隔符（顿号/句号/全角句号）。
    #   例：$ 离 ^{*}7$ .当…… -> 7.当……
    match = MALFORMED_QUESTION_LINE_RE.match(line)

    # 退化路径 2：噪声前缀 + 整数题号 + 无分隔符但紧跟题干关键词。
    #   例：$ \*3$ 根据函数极限的定义…… -> 3.根据……
    #   用于修复 OCR 把一级大题题号包进 $…$ 且无分隔符的情况。
    if match is None:
        match = NOISE_QUESTION_RE.match(line)

    if match is None:
        return line

    prefix = match.group("prefix") if "prefix" in match.groupdict() else match.group("noise")
    number = match.group("number")
    rest = match.group("rest").lstrip(" $}].．、")

    # 仅当行首确实含有 OCR/LaTeX 噪声符号时才归一化，避免把普通文本误判为题号。
    suspicious = any(
        token in prefix
        for token in ("$", "^", "{", "}", "*", "[", "]", "■", "▣", "●", "▪")
    )
    if not suspicious:
        return line

    return f"{number}.{rest}"


def _strip_section_headings(text: str) -> str:
    return SECTION_HEADING_RE.sub("", text).strip()


# ---------- 题型 / 题干 / 答案 / 解析 ----------

def _question_type(section_title: str) -> str:
    title = section_title.replace(" ", "")
    if "多项选择" in title or "多选" in title:
        return "multiple_choice"
    if "选择" in title:
        return "single_choice"
    if "填空" in title:
        return "fill_blank"
    if "证明" in title:
        return "proof"
    if "计算" in title:
        return "calculation"
    if "解答" in title or "综合" in title:
        return "subjective"
    return "other"


def _extract_answer_analysis(text: str) -> tuple[str, str, str]:
    answer_match = ANSWER_MARKER_RE.search(text)
    analysis_match = ANALYSIS_MARKER_RE.search(text)
    markers = [("answer", answer_match), ("analysis", analysis_match)]
    present = [(name, match) for name, match in markers if match is not None]
    if not present:
        return text.strip(), "", ""

    first_start = min(match.start() for _, match in present)
    body = text[:first_start].strip()
    answer = ""
    analysis = ""

    ordered = sorted(present, key=lambda item: item[1].start())
    for index, (name, match) in enumerate(ordered):
        end = ordered[index + 1][1].start() if index + 1 < len(ordered) else len(text)
        value = text[match.end() : end].strip()
        if name == "answer":
            answer = value
        else:
            analysis = value
    return body, answer, analysis


def _extract_options(text: str) -> tuple[str, dict[str, str]]:
    return extract_normalized_options(text)


def _candidate_from_raw(raw_question: RawQuestion) -> QuestionCandidate | None:
    raw = _strip_section_headings(raw_question.raw).strip()
    if not raw:
        return None

    body, answer, analysis = _extract_answer_analysis(raw)
    # 多个独立选择子题必须先保留完整父题结构，不能在父题上全局提取
    # A/B/C/D（同名 key 会被后一个子题覆盖）。真正的局部 option 提取
    # 在 _split_independent_candidates 的结构分支中完成。
    has_choice_subblocks = _choice_subquestion_blocks(body) is not None
    if has_choice_subblocks:
        options: dict[str, str] = {}
    else:
        body, options = _extract_options(body)
    if not body:
        return None

    classification = infer_question_type(
        body, options, section_hint=raw_question.question_type
    )
    review_note = classification.reason if classification.needs_review else ""
    return QuestionCandidate(
        original_number=raw_question.original_number,
        question_type=classification.question_type,
        body=body,
        options=options,
        answer=answer,
        analysis=analysis,
        section_key=raw_question.section_key,
        needs_review=classification.needs_review,
        review_note=review_note,
    )


# ---------- 单页切块 ----------

def _section_type_for_position(markdown: str, position: int) -> str:
    matched_title: str | None = None
    for section in SECTION_HEADING_RE.finditer(markdown):
        if section.start() > position:
            break
        matched_title = section.group(2)
    return _question_type(matched_title) if matched_title else "other"


def _section_key_for_position(markdown: str, position: int) -> str | None:
    """返回当前位置最近的显式章节号（如“一”），用于重复题号消歧。"""
    key: str | None = None
    for section in SECTION_HEADING_RE.finditer(markdown):
        if section.start() > position:
            break
        key = section.group(1)
    return key


def _raw_page_chunks(markdown: str) -> tuple[str, list[RawQuestion]]:
    """返回：本页首个新题号之前的续页内容 + 本页新题块。"""
    cleaned = _clean_ocr_markdown(markdown)
    starts = list(QUESTION_START_RE.finditer(cleaned))

    if not starts:
        return _strip_section_headings(cleaned), []

    preamble = _strip_section_headings(cleaned[: starts[0].start()])
    chunks: list[RawQuestion] = []

    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(cleaned)
        raw = cleaned[start.end() : end].strip()
        raw = _strip_section_headings(raw)
        chunks.append(
            RawQuestion(
                original_number=start.group(1).replace("．", "."),
                raw=raw,
                question_type=_section_type_for_position(cleaned, start.start()),
                section_key=_section_key_for_position(cleaned, start.start()),
            )
        )

    return preamble, chunks


def split_major_questions(markdown: str) -> tuple[str, list[RawQuestion]]:
    """阶段 2：识别本页的大题边界。"""
    return _raw_page_chunks(markdown)


def parse_question_sections(raw_question: RawQuestion) -> QuestionCandidate | None:
    """阶段 3：从大题块中分离题干、选项、答案和解析。"""
    return _candidate_from_raw(raw_question)


def split_page_markdown(markdown: str) -> list[QuestionCandidate]:
    """单页调试接口。跨页合并由 split_pages_into_candidates 负责。"""
    _, chunks = split_major_questions(markdown)
    output: list[QuestionCandidate] = []
    for chunk in chunks:
        candidate = parse_question_sections(chunk)
        if candidate is not None:
            output.append(candidate)
    return output


def _flush_pending(pending: PendingQuestion) -> PlacedCandidate | None:
    candidate = _candidate_from_raw(
        RawQuestion(
            original_number=pending.original_number,
            raw=pending.raw,
            question_type=pending.question_type,
            section_key=pending.section_key,
        )
    )
    if candidate is None:
        return None
    return PlacedCandidate(
        page_number=pending.page_number, candidate=candidate, bbox=pending.bbox
    )


# ---------- 跨页续写判定（防误拼护栏） ----------

# 页眉 / 页脚 / 章节标题 / 出版信息等「家具」文本：不应拼入上一题。
# 注意：这些判定只用于「跨页页首 preamble 是否拼给上一题」，不用于题号识别。
_PAGE_FURNITURE_RE = re.compile(
    r"(?:"
    r"^\s*(参考)?(答案|解析|解答|习题解答|习题答案|习题)\s*[:：]?\s*$"  # 整行仅为答案/解析区标题（无题号）
    r"|^\s*第\s*[一二三四五六七八九十百千0-9]+\s*[章节篇编部分]"            # 第X章/节/篇/部分 标题（不加 \b：中文连续字符间无词边界）
    r"|^\s*第\s*\d+\s*页\s*$"                                               # 第 N 页
    r"|^\s*[-—]\s*\d+\s*[-—]\s*$"                                          # - N -
    r"|^\s*Page\s*\d+\s*$"                                                  # Page N
    r")",
    re.IGNORECASE,
)
_PAGE_FURNITURE_MARKERS = (
    "出版社", "版权", "ISBN", "印次", "www.", "http", "QQ群", "微信", "获取更多", "电话", "邮箱",
)


def _is_page_furniture(text: str) -> bool:
    """判断页首文本是否为页眉/页脚/章节标题/出版信息等家具（而非上一题续写）。"""
    stripped = text.strip()
    if not stripped:
        return False
    if _PAGE_FURNITURE_RE.search(stripped):
        return True
    return any(marker in stripped for marker in _PAGE_FURNITURE_MARKERS)


def _should_join_cross_page(pending_raw: str, preamble: str) -> bool:
    """跨页续写判定：下一页页首 preamble 是否应拼入上一页 pending 题。

    返回 True = 续写（拼入）；False = 页眉/页脚/章节标题等家具（丢弃，不污染上一题）。

    判定（最小、确定、不依赖 LLM）：
    1. preamble 为空 → 无内容，不拼。
    2. preamble 是明显的页眉/页脚/章节标题/出版信息 → 家具，丢弃。
       这是「防误拼」护栏：此前出现过页首异常文字被错误拼进上一题。
    3. 其余一律视为上一题的续写（题干续写或答案续写），
       由后续 splitter 按 解/解析 边界自然截断。

    状态机说明（供理解，不强制每页维护对象）：
    - 当前 pending 题处于 question 还是 answer 段落，由 pending_raw 是否含
      解/解析/答案 标记隐式决定；本函数只拒绝「明显家具」，其余默认续拼，
      与现有「无新题号则续拼」语义一致。
    """
    if not preamble.strip():
        return False
    if _is_page_furniture(preamble):
        return False
    return True


def split_pages_into_candidates(
    pages: Sequence[tuple[int, str]],
    *,
    bbox_provider: BboxProvider | None = None,
) -> list[PlacedCandidate]:
    """把连续多页 Markdown 切成候选大题，含跨页 preamble/pending 合并。

    这是首次 OCR 导入与「重新识别题目」共用的唯一切题入口，
    保证两条路径的切分规则永远一致。

    pages 必须按页码升序，且是一段**连续**的页区间；
    每道题的归属页 = 该题题号首次出现的页码。
    """
    output: list[PlacedCandidate] = []
    pending: PendingQuestion | None = None

    for page_index, (page_number, markdown) in enumerate(pages):
        preamble, chunks = _raw_page_chunks(markdown)

        bbox_by_number: dict[str, dict[str, float] | None] = {}
        if bbox_provider is not None and chunks:
            page_candidates: list[QuestionCandidate] = []
            for chunk in chunks:
                candidate = _candidate_from_raw(chunk)
                if candidate is not None:
                    page_candidates.append(candidate)
            for candidate, bbox in zip(
                page_candidates, bbox_provider(page_number, page_candidates)
            ):
                bbox_by_number[candidate.original_number] = bbox

        # 第 1 页如果首个识别题号是 2，且前面存在明显正文，通常是 OCR
        # 丢失了第 1 题题号。保留为“推断的第 1 题”，交给人工审核确认，
        # 避免整道题静默丢失；后续页仍严格按跨页续文处理。
        if page_index == 0 and pending is None and preamble.strip() and chunks:
            first_number = chunks[0].original_number.split(".", 1)[0]
            if first_number == "2" and re.search(r"(?:解|证明|求|计算|函数|极限|\\lim|\$\$)", preamble):
                inferred = _candidate_from_raw(
                    RawQuestion(original_number="1", raw=preamble, question_type="other")
                )
                if inferred is not None:
                    inferred.needs_review = True
                    inferred.review_note = "第1题题号疑似被 OCR 遗失，系统暂按第1题恢复，请对照原 PDF 确认。"
                    output.append(PlacedCandidate(page_number=page_number, candidate=inferred))
                preamble = ""

        # 当前页首个新题号之前的内容属于上一页最后一道题；
        # 但若它是页眉/页脚等家具文本则丢弃，不污染上一题。
        if pending is not None and preamble and _should_join_cross_page(pending.raw, preamble):
            pending.raw = f"{pending.raw}\n\n{preamble}".strip()

        if not chunks:
            # 空页或整页都是续页内容：保持 pending 不变。
            continue

        # 一旦当前页出现新题，上一页 pending 到这里就完整了。
        if pending is not None:
            placed = _flush_pending(pending)
            if placed is not None:
                output.append(placed)
            pending = None

        # 本页除最后一道外，都已经有明确的下一题边界。
        for chunk in chunks[:-1]:
            candidate = _candidate_from_raw(chunk)
            if candidate is None:
                continue
            output.append(
                PlacedCandidate(
                    page_number=page_number,
                    candidate=candidate,
                    bbox=bbox_by_number.get(candidate.original_number),
                )
            )

        # 本页最后一道暂存，等待下一页确认是否还有续页内容。
        last = chunks[-1]
        pending = PendingQuestion(
            original_number=last.original_number,
            raw=last.raw,
            question_type=last.question_type,
            page_number=page_number,
            bbox=bbox_by_number.get(last.original_number),
            section_key=last.section_key,
        )

    # 页区间结束，最后一道题也可以最终确定。
    if pending is not None:
        placed = _flush_pending(pending)
        if placed is not None:
            output.append(placed)

    return output


def page_has_continuation(markdown: str) -> bool:
    """本页开头是否存在续页内容（首个题号之前的正文）。

    True 表示该页与上一页之间存在跨页 pending 依赖，
    重新切题时不能在这个页边界处断开。
    """
    preamble, _ = _raw_page_chunks(markdown)
    return bool(preamble.strip())


def trace_split_pages(pages: Sequence[tuple[int, str]]) -> SplitTrace:
    """返回切题诊断摘要，帮助人工确认 OCR 修正是否真正进入切题链路。"""
    page_trace: list[dict[str, Any]] = []
    warnings: list[str] = []
    normalized_pages: list[tuple[int, str]] = []
    for page_number, markdown in pages:
        normalized = normalize_page(markdown)
        preamble, chunks = split_major_questions(markdown)
        numbers = [chunk.original_number for chunk in chunks]
        item = {
            "page_number": page_number,
            "normalized_length": len(normalized),
            "preamble_length": len(preamble),
            "major_numbers": numbers,
            "has_continuation": bool(preamble.strip()),
        }
        if preamble.strip() and not normalized_pages:
            warnings.append(
                f"第{page_number}页页首存在未编号正文，可能缺失一级题号；"
                "请对照原 PDF 手动补回题号后重新切题"
            )
        if not chunks and not preamble.strip():
            warnings.append(f"第{page_number}页未识别到题号或续页正文")
        page_trace.append(item)
        normalized_pages.append((page_number, markdown))

    placed = split_pages_into_candidates(normalized_pages)
    candidate_trace = [
        {
            "page_number": item.page_number,
            "original_number": item.candidate.original_number,
            "question_type": item.candidate.question_type,
            "body_length": len(item.candidate.body),
            "answer_length": len(item.candidate.answer),
            "analysis_length": len(item.candidate.analysis),
            "needs_review": item.candidate.needs_review,
            "review_note": item.candidate.review_note,
        }
        for item in placed
    ]
    if not candidate_trace:
        warnings.append("没有生成任何候选题，请检查 OCR 题号或题号修正格式")
    return SplitTrace(page_trace, candidate_trace, warnings)


# ---------- 子题拆分 ----------

# 检测独立子问：(1)...(2)...(3) 格式
SUBCANDIDATE_RE = re.compile(
    r"(?m)^\s*[(（]\s*(\d+)\s*[)）]\s*"
)

# 子问间引用/依赖关键词
SUBQUESTION_DEPEND_RE = re.compile(
    r"(利用|根据|由|借助).*[(（]\s*\d+\s*[)）]|[(（]\s*\d+\s*[)）].*的结论|上[一小题]"
)

# 参考解答中的子题编号：(1)...(2)...，可能带「解/解答/解析」前缀或冒号；
# 也兼容「解(1)」这种前缀紧贴题号的形式（常见于高数答案）。
SUBANSWER_RE = re.compile(
    r"(?m)^\s*(?:解|解答|解析)?[：:]?\s*[(（]\s*(\d+)\s*[)）]"
)

# 题解末尾常有“注本题及下一题……”并再次使用 (1)(2)… 列举方法。
# 这些编号不是子题答案，必须先从答案对齐文本中分离。
TRAILING_NOTE_RE = re.compile(r"(?m)^\s*(注(?:意)?|说明|备注)[：:]?\s*")


def _split_trailing_note(text: str) -> tuple[str, str]:
    match = TRAILING_NOTE_RE.search(text)
    if match is None:
        return text, ""
    return text[:match.start()].rstrip(), text[match.start():].strip()


def _split_answer_for_subquestions(
    text: str,
    sub_numbers: list[str],
) -> tuple[dict[str, str] | None, bool]:
    """把父题的参考解答/解析按子题编号 (1)(2)(3)… 拆成 ``{编号: 片段}``。

    返回 ``(segments, needs_review)``：

    - ``text`` 为空 → ``(None, False)``：无需拆分，不报警；
    - 拆分**可靠**（每个 expected 编号恰好出现一次、集合同、无第二轮重开、
      段间首尾相接无重叠）→ ``(segments, False)``，精确对应；
    - **不可靠**（编号重复 / 含不属于本父题的外来编号 / 缺号 / 无法唯一
      对应）→ ``(None, True)``：调用方应保留原答案并标记需人工核对，
      **绝不覆盖、绝不静默丢弃文本**。

    题号前的公共前缀（``解：`` / ``解析：`` / ``解(1)`` 的「解」）会被自然
    剥离——它们落在首个编号之前或紧贴编号，不属于任何子题答案本身。

    可靠性判定（只要任一不满足即降级，交由人工复核，不自动错配）：
    1. expected 子题编号均能找到；
    2. 每个 expected 编号**只出现一次**——同一 candidate 内不允许两轮
       重复编号（典型坏 case：本父题答案后又拼接了另一道大题的答案，重新
       从 ``(1)`` 开始）；
    3. 答案里出现的编号集合必须**恰好等于**题目子题编号集合——多出外来
       编号说明该段归属不明，静默丢弃会丢文本；
    4. 段与段之间首尾相接、无重叠覆盖。
    """
    if not text or not text.strip():
        return None, False
    matches = list(SUBANSWER_RE.finditer(text))
    if not matches:
        # 完全没有子题编号，无法可靠分配（如答案整体一段）。
        return None, True

    matched_numbers = [m.group(1) for m in matches]

    # 同一编号出现多于一次 ⇒ 存在第二轮重复编号（重新从 (1) 开始），
    # 无法唯一对应，必须降级。
    seen: dict[str, int] = {}
    for n in matched_numbers:
        seen[n] = seen.get(n, 0) + 1
    if any(count > 1 for count in seen.values()):
        return None, True

    # 外来编号仍然不能静默丢弃；但缺少部分答案时，可以安全地对齐已经
    # 识别出的编号，缺失子题留空并标记人工核对，避免把整段答案复制给每题。
    expected_set = set(sub_numbers)
    if not set(matched_numbers).issubset(expected_set):
        return None, True

    # 可靠：按编号切分，段首尾相接、无重叠、无覆盖。
    segments: dict[str, str] = {}
    for idx, match in enumerate(matches):
        number = match.group(1)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        segments[number] = text[start:end].strip()
    return segments, set(matched_numbers) != expected_set


def _choice_subquestion_blocks(
    body: str,
) -> tuple[str, list[tuple[str, str, dict[str, str]]]] | None:
    """识别每个 `(n)` 区间都拥有独立完整 A/B/C/D 的选择子题结构。"""
    matches = list(SUBCANDIDATE_RE.finditer(body))
    if len(matches) < 2:
        return None
    blocks: list[tuple[str, str, dict[str, str]]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        local_body, local_options = _extract_options(body[match.end():end].strip())
        # 保守边界：每个子块都必须各自具备完整的一组 A/B/C/D。
        if set(local_options) != {"A", "B", "C", "D"} or not local_body:
            return None
        blocks.append((match.group(1), local_body, local_options))
    prefix = body[:matches[0].start()].strip()
    return prefix, blocks


def _split_choice_subquestions(
    candidate: QuestionCandidate,
) -> list[QuestionCandidate] | None:
    structured = _choice_subquestion_blocks(candidate.body)
    if structured is None:
        return None
    prefix, blocks = structured
    sub_numbers = [number for number, _, _ in blocks]
    answer_main, answer_note = _split_trailing_note(candidate.answer)
    analysis_main, analysis_note = _split_trailing_note(candidate.analysis)
    answer_segments, answer_flag = _split_answer_for_subquestions(answer_main, sub_numbers)
    analysis_segments, analysis_flag = _split_answer_for_subquestions(analysis_main, sub_numbers)
    needs_review = answer_flag or analysis_flag
    parent_has_answer = bool(answer_main.strip() or analysis_main.strip())
    child_match_status = (
        "ambiguous" if answer_flag or analysis_flag
        else "matched" if parent_has_answer
        else "missing_answer"
    )
    review_note = (
        "子题答案无法可靠拆分（编号缺失、重复或存在歧义），子题答案留空，请人工复核。"
        if needs_review else ""
    )
    children: list[QuestionCandidate] = []
    for sub_number, local_body, local_options in blocks:
        child_body = f"{prefix}\n\n{local_body}".strip()
        classification = infer_question_type(child_body, local_options)
        child_answer = (
            answer_segments.get(sub_number, "") if answer_segments is not None
            else ""
        )
        child_analysis = (
            analysis_segments.get(sub_number, "") if analysis_segments is not None
            else ""
        )
        common_note = "\n\n".join(part for part in (answer_note, analysis_note) if part)
        if common_note:
            child_analysis = f"{child_analysis}\n\n{common_note}".strip()
        children.append(QuestionCandidate(
            original_number=f"{candidate.original_number}({sub_number})",
            question_type=classification.question_type,
            body=child_body,
            options=local_options,
            answer=child_answer,
            analysis=child_analysis,
            needs_review=needs_review or classification.needs_review,
            review_note=review_note or (classification.reason if classification.needs_review else ""),
            section_key=candidate.section_key,
            match_status=child_match_status,
        ))
    return children


def _split_independent_candidates(
    candidate: QuestionCandidate,
) -> list[QuestionCandidate]:
    """判断候选大题是否需要拆分为独立子题。

    保守策略：宁可少拆，不要乱拆。
    - 独立的 (1)(2)(3) 极限/导数计算 → 拆
    - 有依赖/引用的综合题 → 不拆
    - 选择题内 (1)(2) → 不拆
    """
    body = candidate.body

    choice_children = _split_choice_subquestions(candidate)
    if choice_children is not None:
        return choice_children

    # 只有 calculation / proof / short_answer 才考虑拆分
    if candidate.question_type not in ("calculation", "proof", "unknown"):
        return [candidate]

    # 选项存在 → 选择题内部编号，不拆
    if OPTION_TOKEN_RE.search(body):
        return [candidate]

    matches = list(SUBCANDIDATE_RE.finditer(body))
    if len(matches) < 2:
        return [candidate]

    # 有依赖关键词 → composite 不拆
    if SUBQUESTION_DEPEND_RE.search(body):
        return [candidate]

    # 提取子问内容
    detected_numbers = [match.group(1) for match in matches]
    empty_numbers: list[str] = []
    sub_items: list[tuple[str, str]] = []  # (number, text)
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        sub_num = match.group(1)
        sub_text = body[match.end():end].strip()
        if sub_text:
            sub_items.append((sub_num, sub_text))
        else:
            empty_numbers.append(sub_num)

    if len(sub_items) < 2:
        return [candidate]

    number_counts: dict[str, int] = {}
    for number in detected_numbers:
        number_counts[number] = number_counts.get(number, 0) + 1
    numeric_numbers = {int(number) for number in detected_numbers if number.isdigit()}
    missing_numbers = (
        sorted(set(range(1, max(numeric_numbers) + 1)) - numeric_numbers)
        if numeric_numbers and max(numeric_numbers) >= 2 else []
    )
    missing_numbers.extend(number for number in empty_numbers if number not in missing_numbers)
    duplicate_numbers = sorted(number for number, count in number_counts.items() if count > 1)

    # 共同的语义前缀：提取 "求下列极限：" 之类的 prompt
    prefix_end = matches[0].start()
    prefix = body[:prefix_end].strip()
    # 去掉开头的题号数字
    prefix = re.sub(r"^\s*\d+[.．、]\s*", "", prefix)
    if not prefix or len(prefix) < 4:
        # 前缀太短，不拆分
        return [candidate]

    # 题目与参考解答按相同子题编号同步拆分
    sub_numbers = [num for num, _ in sub_items]
    answer_main, answer_note = _split_trailing_note(candidate.answer)
    analysis_main, analysis_note = _split_trailing_note(candidate.analysis)
    answer_segments, answer_flag = _split_answer_for_subquestions(answer_main, sub_numbers)
    analysis_segments, analysis_flag = _split_answer_for_subquestions(analysis_main, sub_numbers)
    needs_review = answer_flag or analysis_flag or bool(missing_numbers or duplicate_numbers)
    parent_has_answer = bool(answer_main.strip() or analysis_main.strip())
    child_match_status = (
        "ambiguous" if answer_flag or analysis_flag
        else "matched" if parent_has_answer
        else "missing_answer"
    )
    structure_notes = []
    if missing_numbers:
        structure_notes.append(f"检测到潜在子题编号，但缺少可构造正文的编号：{','.join(map(str, missing_numbers))}")
    if duplicate_numbers:
        structure_notes.append(f"检测到重复子题编号：{','.join(duplicate_numbers)}")
    review_note = (
        "；".join([
            *structure_notes,
            "子题答案无法可靠拆分，答案/解析留空，请人工复核。"
            if (answer_flag or analysis_flag) else "",
        ]).strip("；")
        if needs_review else ""
    )

    # 构建子候选
    children: list[QuestionCandidate] = []
    for sub_num, sub_text in sub_items:
        child_num = f"{candidate.original_number}({sub_num})"
        child_body = f"{prefix}\n{sub_text}".strip()
        child_answer = (
            answer_segments[sub_num]
            if answer_segments is not None and sub_num in answer_segments
            else ""
        )
        child_analysis = (
            analysis_segments[sub_num]
            if analysis_segments is not None and sub_num in analysis_segments
            else ""
        )
        common_note = "\n\n".join(part for part in (answer_note, analysis_note) if part)
        if common_note:
            child_analysis = f"{child_analysis}\n\n{common_note}".strip()
        children.append(QuestionCandidate(
            original_number=child_num,
            question_type=candidate.question_type,
            body=child_body,
            options={},  # 子题无选项
            answer=child_answer,
            analysis=child_analysis,
            needs_review=needs_review,
            review_note=review_note,
            section_key=candidate.section_key,
            match_status=child_match_status,
        ))

    return children


# ---------- PaddleOCR bbox ----------

def _as_mapping(result: Any) -> dict[str, Any]:
    for attribute in ("json", "to_dict"):
        value = getattr(result, attribute, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if isinstance(value, dict):
            return value
    if isinstance(result, dict):
        return result
    return {}


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _ocr_blocks(result: Any) -> list[tuple[str, list[float]]]:
    mapping = _as_mapping(result)
    for item in _walk(mapping):
        texts = item.get("rec_texts")
        boxes = item.get("rec_boxes") or item.get("dt_polys")
        if isinstance(texts, list) and isinstance(boxes, list) and len(texts) == len(boxes):
            blocks: list[tuple[str, list[float]]] = []
            for text, box in zip(texts, boxes):
                if not isinstance(text, str) or not isinstance(box, (list, tuple)):
                    continue
                flat: list[float] = []
                for value in box:
                    if isinstance(value, (list, tuple)):
                        flat.extend(float(number) for number in value)
                    else:
                        flat.append(float(value))
                if len(flat) >= 4:
                    xs = flat[0::2]
                    ys = flat[1::2]
                    blocks.append((text, [min(xs), min(ys), max(xs), max(ys)]))
            if blocks:
                return blocks
    return []


def _normalize_block_question_number(text: str) -> str:
    """bbox 定位时也做同样的畸形题号修复。"""
    return _normalize_question_line(text.strip())


def _block_has_question_number(text: str, number: str) -> bool:
    normalized = _normalize_block_question_number(text)
    escaped = re.escape(number).replace(r"\.", r"[.．]")
    return bool(re.match(rf"^[ \t]*{escaped}[、.．]", normalized))


def _block_starts_any_question(text: str) -> bool:
    normalized = _normalize_block_question_number(text)
    return bool(QUESTION_START_RE.match(normalized))


def _candidate_bboxes(
    candidates: list[QuestionCandidate], result: Any
) -> list[dict[str, float] | None]:
    blocks = _ocr_blocks(result)
    if not blocks:
        return [None] * len(candidates)

    page_width = max(box[2] for _, box in blocks)
    page_height = max(box[3] for _, box in blocks)
    output: list[dict[str, float] | None] = []
    search_from = 0

    for candidate in candidates:
        start = next(
            (
                index
                for index in range(search_from, len(blocks))
                if _block_has_question_number(blocks[index][0], candidate.original_number)
            ),
            None,
        )
        if start is None:
            output.append(None)
            continue

        end = len(blocks)
        for index in range(start + 1, len(blocks)):
            if _block_starts_any_question(blocks[index][0]):
                end = index
                break

        search_from = end
        boxes = [box for _, box in blocks[start:end]]
        if not boxes:
            output.append(None)
            continue

        x1 = min(box[0] for box in boxes)
        y1 = min(box[1] for box in boxes)
        x2 = max(box[2] for box in boxes)
        y2 = max(box[3] for box in boxes)
        output.append(
            {
                "x": x1,
                "y": y1,
                "width": x2 - x1,
                "height": y2 - y1,
                "page_width": page_width,
                "page_height": page_height,
            }
        )
    return output


# ---------- 入库 ----------

def render_drafts(placed: PlacedCandidate) -> list[RenderedDraft]:
    """候选大题 → 待入库草稿（含二级子题拆分与八段式模板渲染）。

    首次导入与重新切题共用，确保同一份 Markdown 永远得到同样的模板文本。
    """
    candidate = placed.candidate
    rendered: list[RenderedDraft] = []
    for sub in _split_independent_candidates(candidate):
        template = fixed_template(
            sub.body,
            question_type=sub.question_type,
            page_number=placed.page_number,
            original_number=sub.original_number,
            options=sub.options,
            answer=sub.answer,
            analysis=sub.analysis,
            review_note=sub.review_note,
        )
        rendered.append(
            RenderedDraft(
                page_number=placed.page_number,
                original_number=sub.original_number,
                markdown=template,
                bbox=(
                    placed.bbox
                    if sub.original_number == candidate.original_number
                    else None
                ),
                match_status=sub.match_status,
                match_method=sub.match_method,
                review_note=sub.review_note,
            )
        )
    return rendered


def split_candidate_subquestions(candidate: QuestionCandidate) -> list[QuestionCandidate]:
    """公开唯一的子题 splitter，供首次导入、套卷匹配和 resplit 共同复用。"""
    return _split_independent_candidates(candidate)


def persist_rendered_draft(
    database: WorkbenchDatabase, *, source_file_id: str, draft: RenderedDraft
) -> str:
    question_id = f"q_{uuid.uuid4().hex}"
    database.add_question(
        question_id=question_id,
        source_file_id=source_file_id,
        page_number=draft.page_number,
        original_number=draft.original_number,
        bbox=draft.bbox,
        ocr_markdown=draft.markdown,
        match_status=draft.match_status,
        match_method=draft.match_method,
        review_note=draft.review_note,
    )
    return question_id


def _persist_candidate(
    database: WorkbenchDatabase,
    *,
    source_file_id: str,
    placed: PlacedCandidate,
) -> int:
    """持久化候选题目（可能拆分为子题）。返回实际入库的题目数。"""
    drafts = render_drafts(placed)
    for draft in drafts:
        persist_rendered_draft(database, source_file_id=source_file_id, draft=draft)
    return len(drafts)


def _run_lightweight_paddleocr_page(
    image_path: Path, *, timeout_seconds: float
) -> dict[str, Any]:
    """Run the project's existing plain PaddleOCR worker for exactly one page."""
    worker = Path(__file__).resolve().parents[1] / "ocr" / "ocr_worker.py"
    python = Path(sys.executable)
    environment = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2",
    }
    try:
        completed = subprocess.run(
            [str(python), str(worker), str(image_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise OCRPipelineError(
            f"普通 PaddleOCR 单页识别超时（>{timeout_seconds:.0f}s）"
        ) from error

    payload: dict[str, Any] | None = None
    for line in reversed(completed.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "status" in candidate:
            payload = candidate
            break
    if completed.returncode != 0 or payload is None or payload.get("status") != "succeeded":
        detail = ""
        if payload:
            detail = str(payload.get("error") or "; ".join(payload.get("warnings", [])))
        if not detail:
            detail = completed.stderr.strip()[-1000:] or "worker 未返回有效结果"
        raise OCRPipelineError(f"普通 PaddleOCR 单页识别失败：{detail}")
    return payload


def _lightweight_result_markdown(result: dict[str, Any]) -> str:
    """Serialize reading-order OCR blocks as stable page Markdown/text."""
    blocks = result.get("blocks", [])
    lines = [
        str(block.get("original_text", "")).strip()
        for block in blocks
        if str(block.get("original_text", "")).strip()
    ]
    return "\n\n".join(lines).strip() + ("\n" if lines else "")


def _lightweight_result_adapter(result: dict[str, Any]) -> dict[str, Any]:
    """Expose worker blocks in the mapping shape used by existing bbox helpers."""
    texts: list[str] = []
    boxes: list[list[float]] = []
    for block in result.get("blocks", []):
        bbox = block.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        x, y, width, height = (float(value) for value in bbox)
        texts.append(str(block.get("original_text", "")))
        boxes.append([x, y, x + width, y + height])
    return {
        "res": {
            "rec_texts": texts,
            "rec_boxes": boxes,
            "image_width": result.get("image_width"),
            "image_height": result.get("image_height"),
        }
    }


QUESTION_ANCHOR_RE = re.compile(r"^\s*(\d{1,3})[、.．]\s*")


def _page_recall_reordered_markdown(result: dict[str, Any]) -> str:
    """Reorder page-recall blocks by question anchors, preserving raw OCR.

    Exam pages commonly place Q1/Q2 in two columns.  Global y/x reading order
    interleaves those columns, so anchors establish column-local question
    regions first; regions are emitted by numeric question number afterward.
    """
    blocks = []
    for index, block in enumerate(result.get("blocks", [])):
        text = str(block.get("original_text", "")).strip()
        bbox = block.get("bbox")
        if not text or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        x, y, width, height = (float(value) for value in bbox)
        blocks.append({"index": index, "text": text, "x": x, "y": y,
                       "width": width, "height": height})

    anchors = []
    for block in blocks:
        match = QUESTION_ANCHOR_RE.match(block["text"])
        if match:
            anchors.append({**block, "number": int(match.group(1))})
    if not anchors:
        return _lightweight_result_markdown(result)

    # Anchor x-centers define column bands.  This keeps Q1's blocks away from
    # Q2's blocks even when their vertical ranges overlap.
    centers = sorted({round(item["x"] + item["width"] / 2, 1) for item in anchors})
    bands = []
    for index, center in enumerate(centers):
        left = float("-inf") if index == 0 else (centers[index - 1] + center) / 2
        right = float("inf") if index == len(centers) - 1 else (center + centers[index + 1]) / 2
        bands.append((left, right, center))

    def band_for(block: dict[str, Any]) -> float:
        center = block["x"] + block["width"] / 2
        return min(bands, key=lambda band: abs(center - band[2]))[2]

    grouped: dict[int, list[dict[str, Any]]] = {id(item): [] for item in anchors}
    preamble: list[dict[str, Any]] = []
    for block in blocks:
        band = band_for(block)
        candidates = [
            item for item in anchors
            if band == band_for(item)
            and item["y"] - 220 <= block["y"]
            and block["y"] <= item["y"] + 5000
        ]
        if not candidates:
            if block not in anchors:
                preamble.append(block)
            continue
        anchor = max(candidates, key=lambda item: item["y"])
        grouped[id(anchor)].append(block)

    ordered_anchors = sorted(anchors, key=lambda item: (item["number"], item["y"], item["x"]))
    lines: list[str] = []
    lines.extend(item["text"] for item in sorted(preamble, key=lambda item: (item["y"], item["x"])))
    for anchor in ordered_anchors:
        region = grouped[id(anchor)]
        region.sort(key=lambda item: (item["y"], item["x"]))
        anchor_index = next(
            (i for i, item in enumerate(region) if item["index"] == anchor["index"]), None
        )
        if anchor_index is not None:
            region = [
                region[anchor_index],
                *region[:anchor_index],
                *region[anchor_index + 1:],
            ]
        lines.extend(item["text"] for item in region)
    return "\n\n".join(lines).strip() + ("\n" if lines else "")


def run_ocr_into_database(
    pdf_path: Path,
    source_file_id: str,
    database: WorkbenchDatabase,
    *,
    device: str = "cpu",
    raw_root: Path | None = None,
    layout: Any | None = None,
    diagnostics_out: list[Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_callback: Callable[[], bool] | None = None,
    page_timeout_seconds: float = 300.0,
    rss_limit_mb: int = 8192,
    ocr_mode: str = "ppstructure",
) -> tuple[int, int]:
    """统一 OCR 入口；page_recall 是独立的逐页纯 OCR 召回分支。"""
    prepared = prepare_pdf_for_ocr(pdf_path)
    pdf_path = prepared.path
    if ocr_mode == "page_recall":
        result = _run_page_recall_into_database(
            pdf_path, source_file_id, database, raw_root=raw_root, layout=layout,
            diagnostics_out=diagnostics_out, progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
    else:
        if ocr_mode != "ppstructure":
            raise ValueError(f"不支持的 OCR 模式：{ocr_mode}")
        result = _run_unified_ppstructure_into_database(
            pdf_path, source_file_id, database, device=device, raw_root=raw_root,
            layout=layout, diagnostics_out=diagnostics_out,
            progress_callback=progress_callback, cancel_callback=cancel_callback,
        )
    preprocess_root = raw_root or Path("workbench_data/ocr_raw") / source_file_id
    preprocess_root.mkdir(parents=True, exist_ok=True)
    (preprocess_root / "preprocess.json").write_text(
        json.dumps(prepared.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result

    # Legacy implementation retained below for source compatibility only.
    # It is unreachable from the Workbench main entry.
    if layout is None or getattr(layout, "solution_mode", "inline") == "inline":
        return _run_inline_ppstructure_into_database(
            pdf_path,
            source_file_id,
            database,
            device=device,
            raw_root=raw_root,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
    from calculus_agent.ocr.pdf_blank_lines import (
        extract_pdf_horizontal_lines,
        restore_vector_blanks,
    )

    blank_line_diagnostics: dict[int, dict[str, Any]] = {}
    vector_lines = extract_pdf_horizontal_lines(pdf_path, blank_line_diagnostics)
    page_count = 0
    question_count = 0
    pages: list[tuple[int, str]] = []
    bbox_by_page: dict[int, dict[str, dict[str, float] | None]] = {}

    if raw_root is None:
        raw_root = Path("workbench_data/ocr_raw") / source_file_id
    if raw_root.exists():
        shutil.rmtree(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)

    try:
        # OCR and matching are deliberately separate phases.  Each page is
        # rendered, sent to the existing lightweight PaddleOCR subprocess, and
        # persisted before the next page starts.  PPStructureV3 is not used.
        document = pdfium.PdfDocument(str(pdf_path))
        try:
            with tempfile.TemporaryDirectory(prefix="ocr-pages-") as page_dir:
                for page_count in range(1, len(document) + 1):
                    if cancel_callback and cancel_callback():
                        raise OCRPipelineError("用户已取消 OCR（已保留已落盘页面）", page_count=page_count - 1)
                    if progress_callback:
                        progress_callback(page_count, len(document), "ocr")
                    page = document[page_count - 1]
                    # The source PDF already has a large 1368x1920 pt page.
                    # Rendering at 1x keeps the mobile OCR input bounded while
                    # preserving sufficient text resolution for this workflow.
                    bitmap = page.render(scale=1.0)
                    page_image = Path(page_dir) / f"page_{page_count:04d}.png"
                    bitmap.to_pil().save(page_image)
                    del bitmap, page
                    started = time.monotonic()
                    rss_before = _rss_mb()
                    result = _run_lightweight_paddleocr_page(
                        page_image, timeout_seconds=page_timeout_seconds
                    )
                    elapsed = time.monotonic() - started
                    rss_after = _rss_mb()
                    if elapsed > page_timeout_seconds:
                        raise OCRPipelineError(
                            f"第 {page_count} / {len(document)} 页 OCR 超时（>{page_timeout_seconds:.0f}s，实际 {elapsed:.1f}s）",
                            page_count=page_count,
                        )
                    if rss_after > rss_limit_mb:
                        raise OCRPipelineError(
                            f"第 {page_count} / {len(document)} 页 OCR 因内存超过限制而终止（RSS {rss_after:.0f}MB > {rss_limit_mb}MB）",
                            page_count=page_count,
                        )
                    page_markdown = raw_root / f"page_{page_count:04d}.md"
                    markdown = _lightweight_result_markdown(result)
                    result = _lightweight_result_adapter(result)
                    page_markdown.write_text(markdown, encoding="utf-8")
                    page_blank_diagnostics = blank_line_diagnostics.setdefault(page_count, {})
                    markdown = restore_vector_blanks(
                        markdown,
                        result,
                        vector_lines.get(page_count, []),
                        page_blank_diagnostics,
                    )
                    page_markdown.write_text(markdown, encoding="utf-8")
                    diagnostics_path = raw_root / f"page_{page_count:04d}.blank_lines.json"
                    diagnostics_path.write_text(
                        json.dumps(page_blank_diagnostics, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    _, chunks = _raw_page_chunks(markdown)

            # 为本页新题计算起始页 bbox。先按本页独立候选构造一次即可。
                    page_candidates_for_bbox: list[QuestionCandidate] = []
                    for chunk in chunks:
                        candidate = _candidate_from_raw(chunk)
                        if candidate is not None:
                            page_candidates_for_bbox.append(candidate)
                    bbox_list = _candidate_bboxes(page_candidates_for_bbox, result)
                    bbox_by_page[page_count] = {
                        candidate.original_number: bbox
                        for candidate, bbox in zip(page_candidates_for_bbox, bbox_list)
                    }

                    print(
                        f"[OCR] page={page_count}, markdown_chars={len(markdown)}, "
                        f"question_starts={len(chunks)}, elapsed={elapsed:.1f}s, "
                        f"rss_before={rss_before:.0f}MB, rss_after={rss_after:.0f}MB, "
                        f"markdown_saved=yes, raw={page_markdown}"
                    )

                    pages.append((page_count, markdown))
            # 整页原文入库：后续「重新识别题目」以数据库为准，不再依赖磁盘。
                    database.upsert_page(
                        source_file_id, page_count, markdown, reset_edited=True
                    )
                    del result
                    gc.collect()
                    if progress_callback:
                        progress_callback(page_count, len(document), "ocr_page_complete")
        finally:
            document.close()

        def _bbox_provider(
            page_number: int, candidates: list[QuestionCandidate]
        ) -> list[dict[str, float] | None]:
            mapping = bbox_by_page.get(page_number, {})
            return [mapping.get(item.original_number) for item in candidates]

        # 页面布局差异在统一编排层被消化；从 QuestionCandidate 起共用渲染链路。
        if progress_callback:
            progress_callback(page_count, page_count, "matching")
        if layout is None or getattr(layout, "solution_mode", "inline") == "inline":
            placed_candidates = split_pages_into_candidates(pages, bbox_provider=_bbox_provider)
        else:
            from .import_pipeline import import_document

            result = import_document(pages, layout)
            placed_candidates = result.candidates
            if diagnostics_out is not None:
                diagnostics_out.append(result.diagnostics)
        for placed in placed_candidates:
            question_count += _persist_candidate(
                database, source_file_id=source_file_id, placed=placed
            )

    except OCRPipelineError:
        raise
    except Exception as error:
        raise OCRPipelineError(
            f"PaddleOCR 运行失败：{error}", page_count=page_count
        ) from error

    if page_count == 0:
        raise OCRPipelineError("OCR没有解析到任何PDF页面", page_count=0)

    if question_count == 0:
        raise OCRPipelineError(
            "OCR已经完成，但题目切分结果为0。"
            f"原始Markdown已保存在：{raw_root}",
            page_count=page_count,
        )

    return page_count, question_count


def _run_page_recall_into_database(
    pdf_path: Path,
    source_file_id: str,
    database: WorkbenchDatabase,
    *,
    raw_root: Path | None,
    layout: Any | None,
    diagnostics_out: list[Any] | None,
    progress_callback: Callable[[int, int, str], None] | None,
    cancel_callback: Callable[[], bool] | None,
) -> tuple[int, int]:
    """逐页渲染 + 纯 PaddleOCR 召回；后续仍复用统一切题/匹配层。

    This path intentionally does not instantiate PPStructureV3.  Its 1.5x
    page rendering is isolated from the historical path so existing inline
    and separate imports retain their previous OCR behavior.
    """
    if raw_root is None:
        raw_root = Path("workbench_data/ocr_raw") / source_file_id
    if raw_root.exists():
        shutil.rmtree(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)

    document = pdfium.PdfDocument(str(pdf_path))
    total_pages = len(document)
    pages: list[tuple[int, str]] = []
    bbox_by_page: dict[int, dict[str, dict[str, float] | None]] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="page-recall-") as page_dir:
            for page_number in range(1, total_pages + 1):
                if cancel_callback and cancel_callback():
                    raise OCRPipelineError(
                        "用户已取消 OCR（已保留已落盘页面）", page_count=page_number - 1
                    )
                if progress_callback:
                    progress_callback(page_number, total_pages, "ocr")
                page = document[page_number - 1]
                bitmap = page.render(scale=1.5)
                image_path = Path(page_dir) / f"page_{page_number:04d}.png"
                bitmap.to_pil().save(image_path, format="PNG", optimize=True)
                del bitmap, page
                result = _run_lightweight_paddleocr_page(
                    image_path, timeout_seconds=300.0
                )
                raw_markdown = _lightweight_result_markdown(result)
                markdown = _page_recall_reordered_markdown(result)
                adapted = _lightweight_result_adapter(result)
                (raw_root / f"page_{page_number:04d}.raw.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (raw_root / f"page_{page_number:04d}.raw.md").write_text(
                    raw_markdown, encoding="utf-8"
                )
                page_markdown = raw_root / f"page_{page_number:04d}.md"
                page_markdown.write_text(markdown, encoding="utf-8")
                database.upsert_page(source_file_id, page_number, markdown, reset_edited=True)
                pages.append((page_number, markdown))

                _, chunks = _raw_page_chunks(markdown)
                page_candidates = [
                    candidate for chunk in chunks
                    if (candidate := _candidate_from_raw(chunk)) is not None
                ]
                bbox_list = _candidate_bboxes(page_candidates, adapted)
                bbox_by_page[page_number] = {
                    candidate.original_number: bbox
                    for candidate, bbox in zip(page_candidates, bbox_list)
                }
                if progress_callback:
                    progress_callback(page_number, total_pages, "ocr_page_complete")
    except OCRPipelineError:
        raise
    except Exception as error:
        raise OCRPipelineError(
            f"逐页召回 OCR 运行失败：{error}", page_count=len(pages)
        ) from error
    finally:
        document.close()

    if not pages:
        raise OCRPipelineError("OCR没有解析到任何PDF页面", page_count=0)
    if progress_callback:
        progress_callback(len(pages), len(pages), "matching")

    def bbox_provider(page_number: int, candidates: list[QuestionCandidate]):
        mapping = bbox_by_page.get(page_number, {})
        return [mapping.get(item.original_number) for item in candidates]

    if layout is not None and getattr(layout, "solution_mode", "inline") == "separate":
        from .import_pipeline import import_document
        result = import_document(pages, layout)
        placed_candidates = result.candidates
        if diagnostics_out is not None:
            diagnostics_out.append(result.diagnostics)
    else:
        placed_candidates = split_pages_into_candidates(pages, bbox_provider=bbox_provider)

    question_count = sum(
        _persist_candidate(database, source_file_id=source_file_id, placed=placed)
        for placed in placed_candidates
    )
    if question_count == 0:
        raise OCRPipelineError(
            f"逐页召回 OCR 已完成，但题目切分结果为0。原始Markdown已保存在：{raw_root}",
            page_count=len(pages),
        )
    return len(pages), question_count


def _run_unified_ppstructure_into_database(
    pdf_path: Path,
    source_file_id: str,
    database: WorkbenchDatabase,
    *,
    device: str,
    raw_root: Path | None,
    layout: Any | None,
    diagnostics_out: list[Any] | None,
    progress_callback: Callable[[int, int, str], None] | None,
    cancel_callback: Callable[[], bool] | None,
) -> tuple[int, int]:
    """PPStructureV3 -> page Markdown -> layout-specific parser/matcher.

    This is deliberately split into two phases: OCR never sees DocumentLayout
    and never creates Drafts.  The same PPStructure instance handles all pages.
    """
    from paddleocr import PPStructureV3

    if raw_root is None:
        raw_root = Path("workbench_data/ocr_raw") / source_file_id
    if raw_root.exists():
        shutil.rmtree(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)

    document = pdfium.PdfDocument(str(pdf_path))
    total_pages = len(document)
    document.close()
    pages: list[tuple[int, str]] = []
    try:
        parser = PPStructureV3(device=device)
        predictions = parser.predict(input=str(pdf_path))
        for page_number, result in enumerate(predictions, start=1):
            if cancel_callback and cancel_callback():
                raise OCRPipelineError("用户已取消 OCR（已保留已落盘页面）", page_count=page_number - 1)
            if progress_callback:
                progress_callback(page_number, total_pages, "ocr")
            page_markdown = raw_root / f"page_{page_number:04d}.md"
            result.save_to_markdown(page_markdown)
            if not page_markdown.is_file():
                raise OCRPipelineError(
                    f"第 {page_number} 页 OCR 已返回结果，但没有生成 Markdown：{page_markdown}",
                    page_count=page_number,
                )
            markdown = page_markdown.read_text(encoding="utf-8")
            database.upsert_page(source_file_id, page_number, markdown, reset_edited=True)
            pages.append((page_number, markdown))
            if progress_callback:
                progress_callback(page_number, total_pages, "ocr_page_complete")
    except OCRPipelineError:
        raise
    except Exception as error:
        raise OCRPipelineError(f"PPStructure OCR 运行失败：{error}", page_count=len(pages)) from error

    if not pages:
        raise OCRPipelineError("OCR没有解析到任何PDF页面", page_count=0)
    if progress_callback:
        progress_callback(len(pages), len(pages), "matching")

    if layout is not None and getattr(layout, "solution_mode", "inline") == "separate":
        from .import_pipeline import import_document
        result = import_document(pages, layout)
        placed_candidates = result.candidates
        if diagnostics_out is not None:
            diagnostics_out.append(result.diagnostics)
    else:
        placed_candidates = split_pages_into_candidates(pages)

    question_count = 0
    for placed in placed_candidates:
        question_count += _persist_candidate(
            database, source_file_id=source_file_id, placed=placed
        )
    if question_count == 0:
        raise OCRPipelineError(
            f"OCR已经完成，但题目切分结果为0。原始Markdown已保存在：{raw_root}",
            page_count=len(pages),
        )
    return len(pages), question_count


def _run_inline_ppstructure_into_database(
    pdf_path: Path,
    source_file_id: str,
    database: WorkbenchDatabase,
    *,
    device: str,
    raw_root: Path | None,
    progress_callback: Callable[[int, int, str], None] | None,
    cancel_callback: Callable[[], bool] | None,
) -> tuple[int, int]:
    """Original ordinary-exercise path, kept separate from suite matching."""
    from paddleocr import PPStructureV3

    parser = PPStructureV3(device=device)
    page_count = 0
    question_count = 0
    pending: PendingQuestion | None = None
    if raw_root is None:
        raw_root = Path("workbench_data/ocr_raw") / source_file_id
    if raw_root.exists():
        shutil.rmtree(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)

    document = pdfium.PdfDocument(str(pdf_path))
    total_pages = len(document)
    document.close()
    try:
        predictions = parser.predict(input=str(pdf_path))
        for page_count, result in enumerate(predictions, start=1):
            if cancel_callback and cancel_callback():
                raise OCRPipelineError(
                    "用户已取消 OCR（已保留已落盘页面）", page_count=page_count - 1
                )
            if progress_callback:
                progress_callback(page_count, total_pages, "ocr")
            page_markdown = raw_root / f"page_{page_count:04d}.md"
            result.save_to_markdown(page_markdown)
            if not page_markdown.is_file():
                raise OCRPipelineError(
                    f"第 {page_count} 页 OCR 已返回结果，但没有生成 Markdown：{page_markdown}",
                    page_count=page_count,
                )
            markdown = page_markdown.read_text(encoding="utf-8")
            preamble, chunks = _raw_page_chunks(markdown)
            page_candidates = [
                candidate for chunk in chunks
                if (candidate := _candidate_from_raw(chunk)) is not None
            ]
            bbox_by_number = {
                candidate.original_number: bbox
                for candidate, bbox in zip(
                    page_candidates, _candidate_bboxes(page_candidates, result)
                )
            }
            database.upsert_page(source_file_id, page_count, markdown, reset_edited=True)

            print(
                f"[OCR] page={page_count}, markdown_chars={len(markdown)}, "
                f"question_starts={len(chunks)}, raw={page_markdown}"
            )
            if pending is not None and preamble and _should_join_cross_page(pending.raw, preamble):
                pending.raw = f"{pending.raw}\n\n{preamble}".strip()

            if chunks:
                if pending is not None:
                    candidate = _candidate_from_raw(RawQuestion(
                        original_number=pending.original_number,
                        raw=pending.raw,
                        question_type=pending.question_type,
                    ))
                    if candidate is not None:
                        question_count += _persist_candidate(
                            database,
                            source_file_id=source_file_id,
                            placed=PlacedCandidate(
                                pending.page_number, candidate, pending.bbox
                            ),
                        )
                    pending = None

                for chunk in chunks[:-1]:
                    candidate = _candidate_from_raw(chunk)
                    if candidate is not None:
                        question_count += _persist_candidate(
                            database,
                            source_file_id=source_file_id,
                            placed=PlacedCandidate(
                                page_count,
                                candidate,
                                bbox_by_number.get(candidate.original_number),
                            ),
                        )

                last = chunks[-1]
                pending = PendingQuestion(
                    original_number=last.original_number,
                    raw=last.raw,
                    question_type=last.question_type,
                    page_number=page_count,
                    bbox=bbox_by_number.get(last.original_number),
                )
            if progress_callback:
                progress_callback(page_count, total_pages, "ocr_page_complete")

        if progress_callback:
            progress_callback(page_count, page_count, "matching")
        if pending is not None:
            candidate = _candidate_from_raw(RawQuestion(
                original_number=pending.original_number,
                raw=pending.raw,
                question_type=pending.question_type,
            ))
            if candidate is not None:
                question_count += _persist_candidate(
                    database,
                    source_file_id=source_file_id,
                    placed=PlacedCandidate(
                        pending.page_number, candidate, pending.bbox
                    ),
                )
    except OCRPipelineError:
        raise
    except Exception as error:
        raise OCRPipelineError(
            f"PPStructure OCR 运行失败：{error}", page_count=page_count
        ) from error
    if page_count == 0:
        raise OCRPipelineError("OCR没有解析到任何PDF页面", page_count=0)
    if question_count == 0:
        raise OCRPipelineError(
            "OCR已经完成，但题目切分结果为0。"
            f"原始Markdown已保存在：{raw_root}",
            page_count=page_count,
        )
    return page_count, question_count

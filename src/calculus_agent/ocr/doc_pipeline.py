"""文档级 OCR 管线 — PPStructureV3 解析 PDF → 按题切分。

从 pp_structure_test/question_workbench/ocr.py 移植，适配 calculus_knowledge_agent 的
QuestionDraft / Question 模型。
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from calculus_agent.ocr.pdf_preprocess import prepare_pdf_for_ocr


def _repair_ocr_question_prefixes(markdown: str) -> str:
    """修复文档入口中常见的中文 OCR 题号前缀噪声。

    Workbench 保持保守规则；文档级 PPStructure 输出则额外处理真实出现的
    ``$ 得 ^*3$`` / ``河4.`` 形式，再交给统一切题器。
    """
    repaired: list[str] = []
    for line in markdown.splitlines():
        if re.match(r"^\s*\$[^\n]{0,12}\*?\s*(\d{1,3})\$", line):
            line = re.sub(
                r"^\s*\$[^\n]{0,12}\*?\s*(\d{1,3})\$\s*",
                lambda m: f"{m.group(1)}. ",
                line,
                count=1,
            )
        elif re.match(r"^\s*[^\d\-−()（）\s]{1,4}\s*(\d{1,3})[、.．]", line):
            line = re.sub(
                r"^\s*[^\d\-−()（）\s]{1,4}\s*(\d{1,3})([、.．])",
                lambda m: f"{m.group(1)}{m.group(2)}",
                line,
                count=1,
            )
        repaired.append(line)
    return "\n".join(repaired)


# --- 正则（教辅题目切分）---

SECTION_HEADING_RE = re.compile(
    r"(?m)^#{1,4}\s*([一二三四五六七八九十]+)[、.．]\s*([^\n]+)$"
)
QUESTION_START_RE = re.compile(r"(?m)^\s*(\d+(?:[.．]\d+)*)[、.．]\s*(?=\S)")
OPTION_TOKEN_RE = re.compile(r"(?<![A-Za-z])([A-H])[.．、]\s*")
ANSWER_RE = re.compile(r"(?m)^\s*(?:答案|参考答案)\s*[:：]\s*(.*)$")
ANALYSIS_RE = re.compile(r"(?m)^\s*(?:解析|解答|答案解析)\s*[:：]\s*(.*)$")

# 题型中文 → QuestionDraft.question_type
_TYPE_MAP: dict[str, str] = {
    "single_choice": "选择题",
    "multiple_choice": "多选题",
    "fill_blank": "填空题",
    "calculation": "计算题",
    "proof": "证明题",
    "subjective": "解答题",
    "other": "其他",
}


@dataclass
class QuestionCandidate:
    """OCR 切分出的单道题目候选。"""
    original_number: str      # 原始题号，如 "1"、"2.1"
    question_type: str        # single_choice / multiple_choice / ...（内部 key）
    body: str                 # 题干文本（Markdown）
    options: dict[str, str]   # {"A": "选项A内容", ...}
    answer: str               # 答案文本
    analysis: str             # 解析/解答文本
    page_number: int          # 所在页码


# --- 辅助函数 ---

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
    answer_match = ANSWER_RE.search(text)
    analysis_match = ANALYSIS_RE.search(text)
    boundaries = [
        match.start() for match in (answer_match, analysis_match) if match is not None
    ]
    body = text[: min(boundaries)].strip() if boundaries else text.strip()
    answer = ""
    analysis = ""
    if answer_match:
        start = answer_match.end(1) - len(answer_match.group(1))
        end = analysis_match.start() if analysis_match and analysis_match.start() > start else len(text)
        answer = text[start:end].strip()
    if analysis_match:
        analysis = text[analysis_match.end(1) - len(analysis_match.group(1)) :].strip()
    return body, answer, analysis


def _extract_options(text: str) -> tuple[str, dict[str, str]]:
    matches = list(OPTION_TOKEN_RE.finditer(text))
    if len(matches) < 2:
        return text.strip(), {}
    options: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end() : end].strip()
        if value:
            options[match.group(1)] = value
    return text[: matches[0].start()].strip(), options


# --- 公开 API ---

def split_page_markdown(markdown: str, page_number: int) -> list[QuestionCandidate]:
    """从一页 OCR Markdown 中切分出独立题目候选。"""
    sections = list(SECTION_HEADING_RE.finditer(markdown))
    candidates: list[QuestionCandidate] = []
    for section_index, section in enumerate(sections):
        section_end = (
            sections[section_index + 1].start()
            if section_index + 1 < len(sections)
            else len(markdown)
        )
        section_body = markdown[section.end() : section_end]
        starts = list(QUESTION_START_RE.finditer(section_body))
        qtype = _question_type(section.group(2))
        for idx, start in enumerate(starts):
            end = starts[idx + 1].start() if idx + 1 < len(starts) else len(section_body)
            raw = section_body[start.end() : end].strip()
            body, answer, analysis = _extract_answer_analysis(raw)
            body, options = _extract_options(body)
            if body:
                candidates.append(
                    QuestionCandidate(
                        original_number=start.group(1).replace("．", "."),
                        question_type=qtype,
                        body=body,
                        options=options,
                        answer=answer,
                        analysis=analysis,
                        page_number=page_number,
                    )
                )
    return candidates


def run_ppstructure(pdf_path: str, *, prepared_pdf: str | None = None) -> list[str]:
    """Run the historical PPStructureV3 PDF-to-Markdown path."""
    from paddleocr import PPStructureV3

    prepared = prepare_pdf_for_ocr(pdf_path) if prepared_pdf is None else None
    ocr_input = prepared.path if prepared is not None else Path(prepared_pdf)

    parser = PPStructureV3(device="cpu")
    pages: list[str] = []
    with tempfile.TemporaryDirectory(prefix="doc-ocr-") as tmp_dir:
        tmp_root = Path(tmp_dir)
        for page_num, result in enumerate(parser.predict(input=str(ocr_input)), start=1):
            page_md = tmp_root / f"page_{page_num:04d}.md"
            result.save_to_markdown(page_md)
            pages.append(page_md.read_text(encoding="utf-8"))
    return pages


def parse_pdf_to_candidates(
    pdf_path: str, *, prepared_pdf: str | None = None
) -> list[QuestionCandidate]:
    """一站式：PDF → PPStructureV3 → 统一跨页切题。

    文档级导入和 Workbench 必须使用同一套题目边界规则。此前这里逐页调用
    本模块的旧实现，导致 OCR 噪声题号和跨页题目在 ``/ocr/upload-doc`` 路径
    下重新出现。这里保留本模块的候选数据结构，但委托 Workbench 作为唯一
    的切题实现。
    """
    pages = (
        run_ppstructure(pdf_path)
        if prepared_pdf is None
        else run_ppstructure(pdf_path, prepared_pdf=prepared_pdf)
    )
    from calculus_agent.workbench.ocr import split_pages_into_candidates

    placed = split_pages_into_candidates(
        [
            (page_num, _repair_ocr_question_prefixes(markdown))
            for page_num, markdown in enumerate(pages, start=1)
        ]
    )
    all_candidates = [
        QuestionCandidate(
            original_number=item.candidate.original_number,
            question_type=item.candidate.question_type,
            body=item.candidate.body,
            options=item.candidate.options,
            answer=item.candidate.answer,
            analysis=item.candidate.analysis,
            page_number=item.page_number,
        )
        for item in placed
    ]
    if not all_candidates:
        raise RuntimeError("OCR 完成，但未按题号识别到任何独立题目，请人工检查 OCR 原文。")
    return all_candidates

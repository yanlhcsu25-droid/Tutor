from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

from latex2mathml.converter import convert as latex_to_mathml
from pydantic import ValidationError

from .models import MarkdownValidationIssue, QuestionPayload, ValidationResult
from .math_normalization import math_suspicious_issues, normalize_math_format


# 新版模板：题目正文类 section（章节 / 知识点 / 难度不再承载于 OCR Markdown，
# 改由结构化字段 knowledge_points_json / difficulty_level / QuestionKnowledgeLink 提供）。
SECTION_ORDER = [
    "题目内容",
    "参考解答",
    "题型",
    "来源页码",
    "原始题号",
    "审核备注",
]

# 匹配新格式 ## 题目内容 / ## 参考解答，也兼容旧格式 ## 题目 / ## 选项 / ## 答案 / ## 解析
SECTION_RE = re.compile(
    r"(?m)^##\s+(题目内容|参考解答|题目|选项|答案|解析|题型|章节|知识点|难度|来源页码|原始题号|审核备注)\s*$"
)

OPTION_RE = re.compile(r"(?m)^\s*[-*]?\s*([A-H])[.．、:]\s*(.+?)\s*$")
OPTION_BULLET_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)[-*][ \t]+"
    r"(?P<option>[A-H][.．、:][ \t]*[^\n]*?)(?:[ \t]{2})?$"
)
INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.DOTALL)
BLOCK_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


@dataclass
class ParsedMarkdown:
    sections: dict[str, str]
    lines: dict[str, int]


def fixed_template(
    body: str,
    *,
    question_type: str,
    page_number: int,
    original_number: str,
    options: dict[str, str] | None = None,
    answer: str = "",
    analysis: str = "",
    review_note: str = "",
) -> str:
    """生成 OCR 审核 Markdown 模板 — 新版两段式结构。

    question_content = 题干 + 选项
    solution_content = 答案 + 解析（整段）
    """
    option_lines = "  \n".join(
        f"{key}. {value}" for key, value in (options or {}).items()
    )

    # 构建 question_content
    stem = body.strip()
    if option_lines:
        question_content = f"{stem}\n\n{option_lines}"
    else:
        question_content = stem

    # 构建 solution_content（整段）
    solution_parts = []
    if answer and answer.strip():
        solution_parts.append(f"答案：{answer.strip()}")
    if analysis and analysis.strip():
        solution_parts.append(f"解析：\n{analysis.strip()}")
    solution_content = "\n\n".join(solution_parts)

    # 审核提示：仅当存在 review_note 时才渲染，保证无提示时输出与旧版逐字节一致
    # （不影响 resplit 的内容指纹比对）。
    review_section = (
        f"## 审核备注\n\n{review_note.strip()}\n\n" if review_note and review_note.strip()
        else "## 审核备注\n\n"
    )

    return (
        f"## 题目内容\n\n{question_content}\n\n"
        f"## 参考解答\n\n{solution_content}\n\n"
        f"## 题型\n\n{question_type}\n\n"
        f"## 来源页码\n\n{page_number}\n\n"
        f"## 原始题号\n\n{original_number}\n\n"
        f"{review_section}"
    )


def normalize_selection_option_markdown(markdown: str) -> str:
    """Remove list bullets only from option lines inside the question section."""
    matches = list(SECTION_RE.finditer(markdown))
    question = next((match for match in matches if match.group(1) in {"题目内容", "选项"}), None)
    if question is None:
        return markdown
    following = next((match for match in matches if match.start() > question.start()), None)
    end = following.start() if following else len(markdown)
    content = markdown[question.end():end]

    def replace(match: re.Match[str]) -> str:
        return f"{match.group('indent')}{match.group('option').rstrip()}  "

    normalized = OPTION_BULLET_RE.sub(replace, content)
    return f"{markdown[:question.end()]}{normalized}{markdown[end:]}"

def parse_markdown(markdown: str) -> ParsedMarkdown:
    """解析 Markdown 为段落到 dict。

    兼容两套格式：
    - 新版：## 题目内容 + ## 参考解答
    - 旧版：## 题目 + ## 选项 + ## 答案 + ## 解析

    旧版自动合并：
    - 题目 + 选项 → 题目内容
    - 答案 + 解析 → 参考解答
    """
    matches = list(SECTION_RE.finditer(markdown))
    sections: dict[str, str] = {}
    lines: dict[str, int] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections[name] = markdown[start:end].strip()
        lines[name] = markdown.count("\n", 0, match.start()) + 1

    # 兼容旧格式：题目 + 选项 → 题目内容
    if "题目内容" not in sections and "题目" in sections:
        stem = sections.pop("题目", "")
        opts = sections.pop("选项", "")
        if opts:
            sections["题目内容"] = f"{stem}\n\n{opts}"
        else:
            sections["题目内容"] = stem

    # 兼容旧格式：答案 + 解析 → 参考解答
    if "参考解答" not in sections:
        ans = sections.pop("答案", "")
        ana = sections.pop("解析", "")
        parts = []
        if ans:
            parts.append(ans)
        if ana:
            parts.append(ana)
        sections["参考解答"] = "\n\n".join(parts)

    return ParsedMarkdown(sections=sections, lines=lines)


def _field_line(parsed: ParsedMarkdown, field: str) -> int | None:
    mapping = {
        "question_content": "题目内容",
        "solution_content": "参考解答",
        "stem": "题目内容",
        "question_type": "题型",
        "chapter": "章节",
        "knowledge_points": "知识点",
        "difficulty": "难度",
        "page_number": "来源页码",
        "original_number": "原始题号",
        "review_notes": "审核备注",
    }
    return parsed.lines.get(mapping.get(field, ""))


def _latex_expressions(markdown: str) -> list[tuple[str, int]]:
    expressions: list[tuple[str, int]] = []
    without_blocks = markdown
    for match in BLOCK_MATH_RE.finditer(markdown):
        expressions.append((match.group(1).strip(), markdown.count("\n", 0, match.start()) + 1))
    without_blocks = BLOCK_MATH_RE.sub("", without_blocks)
    for match in INLINE_MATH_RE.finditer(without_blocks):
        expressions.append((match.group(1).strip(), without_blocks.count("\n", 0, match.start()) + 1))
    return expressions


def latex_issues(markdown: str) -> list[MarkdownValidationIssue]:
    issues: list[MarkdownValidationIssue] = []
    if markdown.count("$$") % 2:
        issues.append(MarkdownValidationIssue(field="latex", message="块级公式 $$ 未成对闭合"))
        return issues
    single_dollars = len(re.findall(r"(?<!\$)\$(?!\$)", BLOCK_MATH_RE.sub("", markdown)))
    if single_dollars % 2:
        issues.append(MarkdownValidationIssue(field="latex", message="行内公式 $ 未成对闭合"))
        return issues
    for expression, line in _latex_expressions(markdown):
        if not expression:
            issues.append(MarkdownValidationIssue(field="latex", message="公式内容不能为空", line=line))
            continue
        try:
            latex_to_mathml(expression)
        except Exception as error:
            issues.append(
                MarkdownValidationIssue(field="latex", message=f"LaTeX无法渲染：{error}", line=line)
            )
    return issues


def payload_from_markdown(
    markdown: str,
    *,
    question_id: str,
    source_file_id: str,
    ocr_markdown: str,
    source_bbox: dict[str, Any] | None,
) -> tuple[QuestionPayload | None, ValidationResult]:
    """从 Markdown 构建 QuestionPayload。

    新版核心字段：
    - question_content（必填，不能为空）
    - solution_content（可选，为空时 Warning）

    Error 只阻止 question_content 为空。
    """
    parsed = parse_markdown(markdown)
    issues: list[MarkdownValidationIssue] = []
    warnings: list[MarkdownValidationIssue] = []

    # 检查必填章节
    for section in ("题目内容", "题型"):
        if section not in parsed.sections:
            issues.append(
                MarkdownValidationIssue(field=section, message=f"缺少固定模板章节：## {section}")
            )

    issues.extend(latex_issues(markdown))
    warnings.extend(math_suspicious_issues(markdown))

    # 提取核心字段
    question_content = parsed.sections.get("题目内容", "").strip()
    solution_content = parsed.sections.get("参考解答", "").strip()

    # 从 question_content 中分离选项（用于兼容选择题验证）
    options = {
        match.group(1): match.group(2).strip()
        for match in OPTION_RE.finditer(question_content)
    }

    knowledge_points = [
        item.strip()
        for item in re.split(r"[,，、\n]", parsed.sections.get("知识点", ""))
        if item.strip()
    ]
    difficulty_text = parsed.sections.get("难度", "").strip()
    try:
        difficulty = int(difficulty_text) if difficulty_text else None
    except ValueError:
        difficulty = None
    try:
        page_number = int(parsed.sections.get("来源页码", "").strip())
    except ValueError:
        page_number = 0

    # ── Validation ──
    question_type_str = parsed.sections.get("题型", "").strip()

    # Error: question_content 为空
    if not question_content:
        issues.append(MarkdownValidationIssue(
            field="question_content", message="题目内容不能为空"
        ))

    # Warning: solution_content 为空
    if not solution_content:
        warnings.append(MarkdownValidationIssue(
            field="solution_content", message="未识别到参考解答内容"
        ))

    # Warning: 选择题疑似没有选项
    if question_type_str in ("selection", "single_choice", "multiple_choice"):
        if len(options) < 2:
            warnings.append(MarkdownValidationIssue(
                field="options", message="选择题疑似没有合法选项"
            ))

    data: dict[str, Any] = {
        "question_id": question_id,
        "source_file_id": source_file_id,
        "page_number": page_number,
        "original_number": parsed.sections.get("原始题号", "").strip(),
        "question_type": question_type_str,
        "question_content": question_content,
        "solution_content": solution_content,
        # 兼容旧字段
        "stem": question_content,
        "options": options,
        "answer": solution_content,
        "analysis": solution_content,
        "chapter": parsed.sections.get("章节", "").strip(),
        "knowledge_points": knowledge_points,
        "difficulty": difficulty,
        "review_notes": parsed.sections.get("审核备注", "").strip(),
        "source_bbox": source_bbox,
        "ocr_markdown": ocr_markdown,
        "edited_markdown": markdown,
    }
    try:
        payload = QuestionPayload.model_validate(data)
    except ValidationError as error:
        for detail in error.errors():
            field = str(detail["loc"][0]) if detail.get("loc") else "markdown"
            msg = str(detail["msg"])
            line = _field_line(parsed, field)
            # answer 相关的校验错误降级为 Warning
            if field in ("answer",):
                warnings.append(MarkdownValidationIssue(field=field, message=msg, line=line))
            else:
                issues.append(MarkdownValidationIssue(field=field, message=msg, line=line))
        # 只有 Error 才阻止发布（warnings 不阻止）
        if any(i.field not in ("answer",) for i in issues):
            return None, ValidationResult(valid=False, issues=issues, warnings=warnings, parsed=data)
        # 如果只有 answer 相关的 issues，降级后重试
        return None, ValidationResult(valid=True, issues=[], warnings=issues + warnings, parsed=data)

    if issues:
        return None, ValidationResult(valid=False, issues=issues, warnings=warnings, parsed=data)
    return payload, ValidationResult(valid=True, warnings=warnings, parsed=payload.model_dump(mode="json"))


def render_preview(markdown: str) -> tuple[str, list[MarkdownValidationIssue]]:
    normalized = normalize_math_format(markdown)
    issues = latex_issues(normalized) + math_suspicious_issues(markdown)
    escaped = html.escape(normalized)

    def block_math(match: re.Match[str]) -> str:
        try:
            mathml = latex_to_mathml(html.unescape(match.group(1)))
            return f'<div class="math-block">{mathml}</div>'
        except Exception:
            return f'<pre class="math-error">{match.group(0)}</pre>'

    def inline_math(match: re.Match[str]) -> str:
        try:
            return latex_to_mathml(html.unescape(match.group(1)))
        except Exception:
            return f'<code class="math-error">{match.group(0)}</code>'

    escaped = BLOCK_MATH_RE.sub(block_math, escaped)
    escaped = INLINE_MATH_RE.sub(inline_math, escaped)
    output: list[str] = []
    in_list = False
    for line in escaped.splitlines():
        if line.startswith("## "):
            if in_list:
                output.append("</ul>")
                in_list = False
            output.append(f"<h2>{line[3:]}</h2>")
        elif re.match(r"^[-*]\s+", line):
            if not in_list:
                output.append("<ul>")
                in_list = True
            list_text = re.sub(r"^[-*]\s+", "", line)
            output.append(f"<li>{list_text}</li>")
        elif line.strip():
            if in_list:
                output.append("</ul>")
                in_list = False
            if line.startswith('<div class="math-block">'):
                output.append(line)
            else:
                output.append(f"<p>{line}</p>")
    if in_list:
        output.append("</ul>")
    return "\n".join(output), issues

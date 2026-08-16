"""确定性数学格式整理与不确定语义的人工核对提示。"""
from __future__ import annotations

import re

from .models import MarkdownValidationIssue


_ALIGNED_CASES_RE = re.compile(
    r"\\left\\{\s*\\begin\{aligned\}(?P<body>.*?)\\end\{aligned\}\s*\\right\.",
    re.DOTALL,
)

_ALIGNED_RE = re.compile(
    r"\\begin\{aligned\}(?P<body>.*?)\\end\{aligned\}",
    re.DOTALL,
)

_DISPLAY_ALIGNED_RE = re.compile(
    r"\$\$\s*\\begin\{aligned\}(?P<body>.*?)\\end\{aligned\}\s*\$\$",
    re.DOTALL,
)

_ESCAPED_BLANK_RE = re.compile(r"(?<!\\)(?:\\_){3,}")


def normalize_escaped_blank_markers(markdown: str) -> str:
    """Turn OCR/Markdown escaped underscore runs back into visible fill blanks."""
    return _ESCAPED_BLANK_RE.sub(
        lambda match: "_" * match.group(0).count(r"\_"),
        markdown,
    )


def normalize_math_format(markdown: str) -> str:
    """只做可证明等价的格式转换，不改数学数值或方向。"""
    markdown = normalize_escaped_blank_markers(markdown)
    # OCR 常把 x_{0} 识别成 x*{0}，或把带粗体命令的下标拆坏。
    # 仅处理明确的“变量 + *{数字/字母}”形态，避免改动普通乘法。
    markdown = re.sub(r"\\pmb\{([A-Za-z])\}\*\{([^{}]+)\}", r"\1_{\2}", markdown)
    markdown = re.sub(r"(?<![A-Za-z])([A-Za-z])\*\{(\d+)\}", r"\1_{\2}", markdown)

    def convert(match: re.Match[str]) -> str:
        rows = []
        for raw_row in re.split(r"\\\\\s*,?\s*\n?", match.group("body")):
            row = raw_row.strip()
            if not row:
                continue
            row = re.sub(r"^&\s*", "", row)
            row = re.sub(r"\s*&\s*", " , ", row, count=1)
            rows.append(row.rstrip(" ,") + r"\\")
        if not rows:
            return match.group(0)
        return r"\begin{cases}" + "\n".join(rows) + r"\end{cases}"

    normalized = _ALIGNED_CASES_RE.sub(convert, markdown)

    # 长推导在审核预览中按行拆成独立块级公式，避免单个 MathML 表格过宽、
    # 难滚动。每一行末尾的 \\ 是确定的视觉换行边界。
    def split_display_aligned(match: re.Match[str]) -> str:
        blocks: list[str] = []
        for raw_row in re.split(r"\\\\\s*", match.group("body")):
            row = raw_row.strip()
            if not row:
                continue
            row = re.sub(r"^\s*&\s*", "", row)
            blocks.append(f"$$\n{row}\n$$")
        return "\n\n".join(blocks) if blocks else match.group(0)

    normalized = _DISPLAY_ALIGNED_RE.sub(split_display_aligned, normalized)

    # latex2mathml 会把 aligned 中的 & 当成普通字符。array 的 MathML
    # 转换是完整的，因此预览时做等价环境替换并保留原有列分隔与 \\ 换行。
    def aligned_to_array(match: re.Match[str]) -> str:
        body = match.group("body")
        max_separators = max((row.count("&") for row in re.split(r"\\\\", body)), default=0)
        column_specs = {0: "l", 1: "rl", 2: "rcl"}
        columns = column_specs.get(max_separators, "l" * (max_separators + 1))
        return rf"\begin{{array}}{{{columns}}}{body}\end{{array}}"

    return _ALIGNED_RE.sub(aligned_to_array, normalized)


def math_suspicious_issues(markdown: str) -> list[MarkdownValidationIssue]:
    """发现常见 OCR 语义风险，仅警告，不自动补全公式。"""
    issues: list[MarkdownValidationIssue] = []
    ordinary_limits = re.findall(
        r"\\lim_\{([^}]*)\}[^\n=]*=\s*([+-]?\d+(?:\.\d+)?)", markdown
    )
    for index, (target, value) in enumerate(ordinary_limits):
        if index == 0:
            continue
        previous_target, previous_value = ordinary_limits[index - 1]
        if target == previous_target and value != previous_value:
            issues.append(MarkdownValidationIssue(
                field="math_semantics",
                message=(
                    f"疑似 OCR 遗失左右极限符号：连续出现 x→{target} 的普通极限，"
                    "但结果不一致，请对照原 PDF（不要自动补全 ^- 或 ^+）。"
                ),
            ))
            break
    return issues

"""Long-lived, content-only classifier for calculus business question types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ClassificationResult:
    question_type: str
    reason: str
    needs_review: bool = False


SELECTION_CUE_RE = re.compile(
    r"选择(?:正确的|一个正确|正确结论)|选出(?:一个)?正确|从.{0,16}(?:结论|说法|选项).{0,8}选出|"
    r"以下(?:说法|结论).{0,8}正确"
)
PROOF_RE = re.compile(r"(?:试证明|试证|证明下列|根据.{0,30}证明|利用.{0,30}证明|证明)")
FILL_EXPLICIT_RE = re.compile(r"填空|填入|填在空格|在空格内")
FILL_END_RE = re.compile(
    r"(?:则\s*\$?[A-Za-zα-ωΑ-Ω][\wα-ωΑ-Ω]*\$?\s*=\s*\$?(?=解|答案|[。．.]|$)|"
    r"其值为|结果为|应为)\s*(?:[。．.]|$)?"
)
CALCULATION_RE = re.compile(
    r"(?:计算|求(?:下列|出|解|函数|极限|导数|导数值|积分|值|通解|面积|体积|最大值|最小值|"
    r"单调区间|间断点)?|讨论函数.{0,120}连续|确定参数|研究函数性质|"
    r"应当怎样选择.{0,20}才能|问.{0,30}(?:多少|等于)|作出.{0,20}图形|作图)"
)
SUBJECTIVE_RE = re.compile(
    r"(?:哪些是对的.{0,20}哪些是错的|判断.{0,20}(?:对错|正误)|说明理由|"
    r"给出一个反例|举出.{0,30}(?:例子|实例)|哪一个是.{0,30}|简述|解释(?:下列|说明))"
)
OPTION_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])(?:[(（]\s*)?([A-H])\s*(?:[)）](?:[.．、])?|[.．、])\s*"
)


def extract_normalized_options(text: str) -> tuple[str, dict[str, str]]:
    """Extract common OCR option forms and normalize their keys to A-H."""
    matches = list(OPTION_TOKEN_RE.finditer(text))
    if len(matches) < 2:
        return text.strip(), {}
    options: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end():end].strip()
        if value:
            options[match.group(1)] = value
    return text[:matches[0].start()].strip(), options


def infer_question_type(
    question_content: str,
    options: Mapping[str, str] | None = None,
    *,
    section_hint: str | None = None,
) -> ClassificationResult:
    """Classify without changing splitting, Markdown, answers, or matching state."""
    text = re.sub(r"\s+", "", question_content)
    if options is None:
        _, detected_options = extract_normalized_options(question_content)
        options = detected_options
    option_count = len(options)

    if option_count >= 2 and (option_count >= 4 or SELECTION_CUE_RE.search(text)):
        return ClassificationResult("selection", f"识别到{option_count}个规范化选项")
    if SELECTION_CUE_RE.search(text) and option_count >= 2:
        return ClassificationResult("selection", "题干包含选择指令且识别到多个选项")
    if PROOF_RE.search(text):
        return ClassificationResult("proof", "题干包含明确的证明指令")
    explicit_fill = FILL_EXPLICIT_RE.search(text)
    if explicit_fill:
        return ClassificationResult("fill_blank", f"题干包含“{explicit_fill.group(0)}”")
    fill_end = FILL_END_RE.search(text)
    if fill_end:
        return ClassificationResult("fill_blank", f"题干以待填写表达“{fill_end.group(0)}”结束")
    calculation = CALCULATION_RE.search(text)
    if calculation:
        return ClassificationResult("calculation", f"题干包含开放求解指令“{calculation.group(0)}”")
    subjective = SUBJECTIVE_RE.search(text)
    if subjective:
        return ClassificationResult("subjective", f"题干包含主观判断或阐释指令“{subjective.group(0)}”")

    hint = (section_hint or "").replace(" ", "")
    if hint in {"single_choice", "multiple_choice", "selection"} and option_count >= 2:
        return ClassificationResult("selection", "选择题章节中识别到多个选项")
    if hint == "fill_blank":
        return ClassificationResult("fill_blank", "章节标题为填空题")
    if hint == "proof":
        return ClassificationResult("proof", "章节标题为证明题")
    if hint == "calculation":
        return ClassificationResult("calculation", "章节标题为计算题")
    if hint == "subjective":
        return ClassificationResult("subjective", "章节标题为解答题")
    if "选择" in hint and option_count >= 2:
        return ClassificationResult("selection", "章节标题为选择题且识别到多个选项")
    if "填空" in hint:
        return ClassificationResult("fill_blank", "章节标题为填空题")
    if "证明" in hint:
        return ClassificationResult("proof", "章节标题为证明题")
    if "计算" in hint:
        return ClassificationResult("calculation", f"章节标题为{hint}")
    if any(token in hint for token in ("解答", "综合")):
        return ClassificationResult("subjective", f"章节标题为{hint}")
    return ClassificationResult("unknown", "未发现可靠的题型特征", needs_review=True)

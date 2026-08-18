from io import BytesIO
import os
from pathlib import Path
import re
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from calculus_agent.schemas import PaperPreviewRead

FONT = "MathPaperCJK"
_FONT_CANDIDATES = [
    os.getenv("MATH_PAPER_FONT_PATH"),
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def _register_cjk_font() -> str:
    errors = []
    for path in _FONT_CANDIDATES:
        if not path or not Path(path).is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(FONT, path))
            return path
        except (OSError, TTFError) as error:
            errors.append(f"{path}: {error}")
    detail = f"；已尝试：{' | '.join(errors)}" if errors else ""
    raise RuntimeError(f"未找到ReportLab兼容的中文TrueType字体，请设置MATH_PAPER_FONT_PATH{detail}")


_font_path = _register_cjk_font()


def render_paper_pdf(paper: PaperPreviewRead, *, teacher_version: bool) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=paper.title,
        author="Math Paper Agent",
    )
    styles = _styles()
    story = [
        Spacer(1, 2 * mm),
        Paragraph(
            escape(paper.title + ("（教师解析卷）" if teacher_version else "")), styles["title"]
        ),
        Spacer(1, 7 * mm),
    ]
    current_type = None
    section_index = 0
    section_number = 0
    for item in paper.items:
        if item.question_type != current_type:
            current_type = item.question_type
            section_index = 0
            section_number += 1
            story.extend(
                [
                    Paragraph(
                        _section_title(
                            paper,
                            current_type,
                        ),
                        styles["section"],
                    ),
                    Spacer(1, 3 * mm),
                ]
            )
        section_index += 1
        question_flowables = _question_flowables(item, section_index, styles)
        if teacher_version:
            question_flowables.extend(_teacher_answer_flowables(item, styles))
        if item.question_type in {"计算题", "证明题"} and not teacher_version:
            answer_height = _answer_space_mm(item.score) * mm
            story.extend(
                [
                    CondPageBreak(answer_height + 24 * mm),
                    KeepTogether([*question_flowables, Spacer(1, answer_height)]),
                ]
            )
        else:
            story.extend(
                [
                    *question_flowables,
                    Spacer(1, 6 * mm if item.question_type == "选择题" else 4 * mm),
                ]
            )

    doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
    return buffer.getvalue()


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "title",
            fontName=FONT,
            fontSize=18,
            leading=25,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#0F172A"),
        ),
        "section": ParagraphStyle(
            "section",
            fontName=FONT,
            fontSize=12,
            leading=19,
            spaceBefore=3,
            textColor=colors.black,
            borderPadding=(0, 0, 0, 0),
            leftIndent=0,
        ),
        "question": ParagraphStyle(
            "question",
            fontName=FONT,
            fontSize=10.8,
            leading=19,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#111827"),
            wordWrap="CJK",
        ),
        "option": ParagraphStyle(
            "option",
            fontName=FONT,
            fontSize=10.5,
            leading=17,
            textColor=colors.HexColor("#111827"),
            wordWrap="CJK",
        ),
        "answer_label": ParagraphStyle(
            "answer_label",
            fontName=FONT,
            fontSize=10.5,
            leading=18,
            textColor=colors.HexColor("#1D4ED8"),
            spaceAfter=3,
            leftIndent=8 * mm,
        ),
        "answer": ParagraphStyle(
            "answer",
            fontName=FONT,
            fontSize=10.5,
            leading=18,
            textColor=colors.HexColor("#1F2937"),
            wordWrap="CJK",
            leftIndent=8 * mm,
        ),
        "meta": ParagraphStyle(
            "meta",
            fontName=FONT,
            fontSize=9,
            leading=16,
            textColor=colors.HexColor("#64748B"),
            wordWrap="CJK",
            leftIndent=8 * mm,
        ),
    }


def _section_title(paper: PaperPreviewRead, question_type: str) -> str:
    items = [item for item in paper.items if item.question_type == question_type]
    score = sum(item.score for item in items)
    section_types = list(dict.fromkeys(
        item.question_type for item in paper.items
    ))
    section_number = section_types.index(question_type) + 1
    chinese_numbers = {
        1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
        6: "六", 7: "七", 8: "八", 9: "九", 10: "十",
    }
    number = chinese_numbers.get(section_number, str(section_number))
    descriptions = {
        "选择题": "每小题给出的选项中，只有一个选项正确。",
        "多选题": "每小题给出的选项中，有多项符合题目要求。",
        "填空题": "请将答案填写在题中横线上。",
        "计算题": "解答应写出必要的计算步骤。",
        "证明题": "证明应写出完整的推理过程。",
    }
    average = (
        items[0].score
        if items and all(item.score == items[0].score for item in items)
        else None
    )
    per_item = (
        f"，每小题 {_format_score(average)} 分"
        if average is not None else ""
    )
    return escape(
        f"{number}、{question_type}（本大题共 {len(items)} 小题"
        f"{per_item}，共 {_format_score(score)} 分。"
        f"{descriptions.get(question_type, '')}）"
    )

def _answer_space_mm(score: float) -> float:
    return max(32, min(72, 20 + score * 3))


def _format_score(score: float) -> str:
    return f"{score:g}"


_OPTION_PATTERN = re.compile(r"^[A-D][.、．]\s*")


def _question_flowables(item, index: int, styles: dict[str, ParagraphStyle]) -> list:
    lines = [line.strip() for line in item.question_text.splitlines() if line.strip()]
    options = [line for line in lines if _OPTION_PATTERN.match(line)]
    stem_lines = [line for line in lines if not _OPTION_PATTERN.match(line)]
    prefix = (
        f"{index}.（本小题满分 {_format_score(item.score)} 分）"
        if item.question_type in {"计算题", "证明题"}
        else f"{index}. "
    )
    result = [
        Paragraph(
            prefix + escape(" ".join(stem_lines)),
            styles["question"],
        )
    ]
    if options:
        columns = 4 if len(options) == 4 else min(2, len(options))
        cells = [Paragraph(escape(option), styles["option"]) for option in options]
        while len(cells) % columns:
            cells.append("")
        rows = [cells[start : start + columns] for start in range(0, len(cells), columns)]
        result.extend(
            [
                Spacer(1, 2 * mm),
                Table(
                    rows,
                    colWidths=[(174 / columns) * mm] * columns,
                    style=TableStyle(
                        [
                            ("FONTNAME", (0, 0), (-1, -1), FONT),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 2),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                        ]
                    ),
                ),
            ]
        )
    return result


def _teacher_answer_flowables(item, styles: dict[str, ParagraphStyle]) -> list:
    result = [
        Spacer(1, 2 * mm),
        Paragraph(
            f"答案：{escape(item.final_answer or '暂无独立答案')}", styles["answer_label"]
        ),
    ]
    if item.solution_steps:
        result.append(Paragraph("解析：", styles["answer_label"]))
        result.extend(
            Paragraph(f"{index}. {escape(step)}", styles["answer"])
            for index, step in enumerate(item.solution_steps, start=1)
        )
    if item.knowledge:
        result.append(Paragraph(f"知识点：{escape('、'.join(item.knowledge))}", styles["meta"]))
    return result


def _page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont(FONT, 9)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawCentredString(A4[0] / 2, 10 * mm, f"第 {doc.page} 页")
    canvas.restoreState()

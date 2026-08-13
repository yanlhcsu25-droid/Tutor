from html import unescape
import re
from pathlib import Path

from calculus_agent.question_types import canonical_question_type
from calculus_agent.schemas import PaperPreviewRead


_TEACHER_TEMPLATE = Path(__file__).resolve().parents[3] / "templates" / "latex" / "teacher_exam.tex"


def render_paper_latex(paper: PaperPreviewRead, *, teacher_version: bool) -> str:
    if teacher_version:
        return _render_teacher_template(paper)

    title = _latex_text(paper.title)
    suffix = ""
    sections: list[str] = []
    current_type = None
    section_number = 0
    for question_number, item in enumerate(paper.items, start=1):
        question_type = canonical_question_type(item.question_type)
        answer_space = _answer_space(question_type, item.score)
        # Student pages reserve room for handwritten answers.  Teacher pages
        # should let the question/answer/explanation flow naturally instead of
        # inheriting that score-based reservation.
        needed = (
            min(answer_space + 2.2, 20.0)
        )
        if question_type != current_type:
            current_type = question_type
            section_number += 1
            sections.append(f"\\Needspace{{{needed:.1f}cm}}")
            sections.append(
                f"\\section*{{{_section_title(paper, current_type, section_number)}}}"
            )
        else:
            sections.append(f"\\Needspace{{{needed:.1f}cm}}")
        stem, options = _question_parts(item.question_text)
        sections.extend(
            [
                f"\\question{{{question_number}}}{{{item.score:g}}}{{{_mixed_latex(stem)}}}",
                _options_table(options),
            ]
        )
        sections.append(_student_answer_latex(question_type, answer_space))

    body = "\n\n".join(sections)
    return rf"""\documentclass[UTF8,12pt,a4paper]{{ctexart}}
\usepackage{{amsmath,amssymb,mathtools}}
\usepackage[left=2cm,right=2cm,top=1.8cm,bottom=1.8cm,headheight=15pt]{{geometry}}
\usepackage{{enumitem,fancyhdr,xcolor,lastpage,needspace,tabularx,array}}
\definecolor{{accent}}{{HTML}}{{111827}}
\definecolor{{slate}}{{HTML}}{{64748B}}
\pagestyle{{fancy}}
\fancyhf{{}}
\fancyfoot[C]{{\small\textcolor{{slate}}{{第 \thepage\ 页，共 \pageref{{LastPage}} 页}}}}
\renewcommand{{\headrulewidth}}{{0pt}}
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{0.45em}}
\setlength{{\fboxsep}}{{7pt}}
\newcommand{{\question}}[3]{{\noindent\textbf{{#1.}}\hspace{{0.35em}}#3\hfill\textcolor{{slate}}{{（#2分）}}\par}}
\newenvironment{{teacheranswer}}{{\begin{{quote}}\small\color{{accent}}\hrule\vspace{{0.55em}}}}{{\vspace{{0.35em}}\hrule\end{{quote}}}}
\ctexset{{section={{format=\normalsize\bfseries\color{{accent}},beforeskip=1.1em,afterskip=0.7em}}}}

\begin{{document}}
\begin{{center}}
  {{\LARGE\bfseries {title}{suffix}}}
\end{{center}}
\vspace{{0.6em}}
\noindent\textbf{{满分：{paper.total_score:g} 分}}\hfill
\\textbf{{考试信息以任课教师要求为准}}
\par
\vspace{{0.7em}}
\\noindent 姓名：\\underline{{\\hspace{{3.2cm}}}}\\hfill 学号：\\underline{{\\hspace{{3.2cm}}}}\\hfill 班级：\\underline{{\\hspace{{2.6cm}}}}\\par
\vspace{{0.8em}}
\hrule
\vspace{{0.5em}}

{body}

\end{{document}}
"""


def _render_teacher_template(paper: PaperPreviewRead) -> str:
    template = _TEACHER_TEMPLATE.read_text(encoding="utf-8")
    body: list[str] = []
    current_type = None
    section_number = 0
    for question_number, item in enumerate(paper.items, start=1):
        question_type = canonical_question_type(item.question_type)
        if question_type != current_type:
            current_type = question_type
            section_number += 1
            body.append(
                f"\\ExamSection{{{_chinese_number(section_number)}}}"
                f"{{{_section_description(paper, question_type)}}}"
            )
            body.append("\\begin{ExamQuestions}")
        stem, options = _question_parts(item.question_text)
        body.append(f"\\ExamQuestion{{{item.score:g}}}{{}}")
        body.append(_mixed_latex(format_question_display(stem)))
        if options:
            body.append("\\begin{ExamOptions}")
            body.extend(
                f"\\item {_mixed_latex(_OPTION_PATTERN.sub('', option, count=1))}"
                for option in options
            )
            body.append("\\end{ExamOptions}")
        body.extend(_teacher_answer_latex(item))
        next_type = (
            canonical_question_type(paper.items[question_number].question_type)
            if question_number < len(paper.items)
            else None
        )
        if next_type != current_type:
            body.append("\\end{ExamQuestions}")

    return (
        template.replace("%__PAPER_TITLE__%", _latex_text(paper.title))
        .replace("%__PAPER_META__%", f"满分：{paper.total_score:g} 分\\quad 本卷附参考答案与解析")
        .replace("%__PAPER_BODY__%", "\n\n".join(body))
    )


def _section_description(paper: PaperPreviewRead, question_type: str) -> str:
    title = _section_title(paper, question_type, 1)
    return title.split("、", 1)[1]


def _teacher_answer_latex(item) -> list[str]:
    answer = _mixed_latex(item.final_answer or "暂无独立答案")
    result = ["\\begin{TeacherAnswer}", f"\\textbf{{答案：}}{answer}\\par"]
    if item.solution_steps:
        result.append("\\textbf{解析：}")
        result.append("\\begin{enumerate}[label=\\arabic*.,leftmargin=2em,topsep=0.2em]")
        result.extend(
            f"\\item {_mixed_latex(cleaned)}"
            for step in item.solution_steps
            if (cleaned := _strip_solution_prefix(step))
        )
        result.append("\\end{enumerate}")
    if item.knowledge:
        knowledge = _latex_text("、".join(item.knowledge))
        result.append(f"\\noindent\\textcolor{{slate}}{{知识点：{knowledge}}}\\par")
    result.extend(["\\end{TeacherAnswer}", "\\vspace{0.5cm}"])
    return result


def _strip_solution_prefix(step: str) -> str:
    """Remove one legacy label only when it occurs at the start of a step."""
    return re.sub(r"^\s*(?:解析|解|证明)\s*[:：]\s*", "", step, count=1)


def _student_answer_latex(question_type: str, height: float) -> str:
    if question_type in {"选择题", "多选题"}:
        return "\\vspace{0.35cm}"
    if question_type == "填空题":
        return "\\vspace{0.65cm}"
    return f"\\par\\vspace{{{height:.1f}cm}}"


def _answer_space(question_type: str, score: float) -> float:
    if question_type in {"选择题", "多选题"}:
        return 0.35
    if question_type == "填空题":
        return 0.65
    if question_type == "证明题":
        return max(5.5, min(11.0, 3.0 + score * 0.42))
    return max(3.5, min(9.0, 2.2 + score * 0.34))


def _section_title(
    paper: PaperPreviewRead, question_type: str, section_number: int
) -> str:
    items = [
        item
        for item in paper.items
        if canonical_question_type(item.question_type) == question_type
    ]
    score = sum(item.score for item in items)
    number = _chinese_number(section_number)
    descriptions = {
        "选择题": "每小题给出的选项中，只有一个选项正确。",
        "多选题": "每小题给出的选项中，有多项符合题目要求。",
        "填空题": "请将答案填写在题中横线上。",
        "计算题": "解答应写出必要的计算步骤。",
        "证明题": "证明应写出完整的推理过程。",
        "解答题": "解答应写出文字说明、证明过程或演算步骤。",
    }
    average = items[0].score if items and all(item.score == items[0].score for item in items) else None
    per_item = f"，每小题 {average:g} 分" if average is not None else ""
    return _latex_text(
        f"{number}、{question_type}（本大题共 {len(items)} 小题{per_item}，共 {score:g} 分。"
        f"{descriptions.get(question_type, '')}）"
    )


def _chinese_number(value: int) -> str:
    values = "零一二三四五六七八九十"
    if 0 <= value <= 10:
        return values[value]
    return str(value)


_MATH_PATTERN = re.compile(r"(\$\$.*?\$\$|\$.*?\$|\\\[.*?\\\]|\\\(.*?\\\))", re.DOTALL)
_OPTION_PATTERN = re.compile(r"^[A-D][.、．]\s*")


def _question_parts(value: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    options = [line for line in lines if _OPTION_PATTERN.match(line)]
    stem = "\n".join(line for line in lines if not _OPTION_PATTERN.match(line))
    return stem, options


_SUBQUESTION_PATTERN = re.compile(r"^\((\d+)\)\s*(.*)$")
_SUBQUESTION_INLINE_PATTERN = re.compile(r"(?:^|(?<=[：:；;]))[ \t]*\((\d+)\)")
_BLANK_TOKEN = "§EXAM_BLANK§"


def format_question_display(value: str) -> str:
    """Preserve deterministic question structure for the visual template."""
    ordinary: list[str] = []
    formatted: list[str] = []

    def flush_ordinary() -> None:
        if ordinary:
            formatted.append(" ".join(ordinary))
            ordinary.clear()

    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        chunks = _split_subquestion_line(line)
        for index, chunk in enumerate(chunks):
            match = _SUBQUESTION_PATTERN.match(chunk)
            if match:
                flush_ordinary()
                number, content = match.groups()
                formatted.append(f"({number}) {_replace_blanks(content)}")
            elif chunk.strip():
                ordinary.append(_replace_blanks(chunk.strip()))
    flush_ordinary()
    return "\n".join(formatted)


def _split_subquestion_line(line: str) -> list[str]:
    """Split only explicit subquestion markers outside math environments."""
    math_parts: list[str] = []
    cursor = 0
    protected: list[str] = []
    for index, match in enumerate(_MATH_PATTERN.finditer(line)):
        protected.append(line[cursor : match.start()])
        token = f"§EXAM_MATH_{index}§"
        protected.append(token)
        math_parts.append(match.group(0))
        cursor = match.end()
    protected.append(line[cursor:])
    safe_line = "".join(protected)

    markers = list(_SUBQUESTION_INLINE_PATTERN.finditer(safe_line))
    if not markers:
        return [line]

    chunks: list[str] = [safe_line[: markers[0].start()]]
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(safe_line)
        chunks.append(f"({marker.group(1)}) {safe_line[marker.end() : end]}")

    restored: list[str] = []
    for chunk in chunks:
        for index, math in enumerate(math_parts):
            chunk = chunk.replace(f"§EXAM_MATH_{index}§", math)
        restored.append(chunk)
    return restored


def _replace_blanks(value: str) -> str:
    parts = _MATH_PATTERN.split(value)
    return "".join(
        part if _MATH_PATTERN.fullmatch(part) else part.replace("____", _BLANK_TOKEN)
        for part in parts
    )


def _options_table(options: list[str]) -> str:
    if not options:
        return ""
    if len(options) == 4:
        cells = " & ".join(_mixed_latex(option) for option in options)
        return (
            "\\begin{tabularx}{\\textwidth}{@{}>{\\raggedright\\arraybackslash}X"
            ">{\\raggedright\\arraybackslash}X>{\\raggedright\\arraybackslash}X"
            ">{\\raggedright\\arraybackslash}X@{}}\n"
            f"{cells}\n\\end{{tabularx}}"
        )
    return "\\\\\n".join(_mixed_latex(option) for option in options)


def _mixed_latex(value: str) -> str:
    """Escape prose while preserving explicit LaTeX math segments."""
    value = unescape(value)
    rendered_lines: list[str] = []
    for line in value.splitlines():
        match = _SUBQUESTION_PATTERN.match(line.strip())
        if match:
            number, content = match.groups()
            rendered_lines.append(
                f"\\ExamSubQuestion{{{number}}}{{{_mixed_latex_inline(content)}}}"
            )
        else:
            rendered_lines.append(_mixed_latex_inline(line))
    return "\n".join(rendered_lines)


def _mixed_latex_inline(value: str) -> str:
    parts = _MATH_PATTERN.split(value)
    rendered: list[str] = []
    for part in parts:
        if _MATH_PATTERN.fullmatch(part):
            rendered.append(part)
            continue
        prose_parts = part.split(_BLANK_TOKEN)
        rendered.append(r"\underline{\hspace{2cm}}".join(_latex_text(piece) for piece in prose_parts))
    return "".join(rendered)


def _latex_text(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)

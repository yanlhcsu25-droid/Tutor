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
        needed = min(answer_space + 2.2, 20.0)
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
                f"\\question{{{question_number}}}{{{item.score:g}}}"
                f"{{{_mixed_latex(format_question_display(stem))}}}",
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
\setlength{{\emergencystretch}}{{3em}}
\allowdisplaybreaks
\providecommand{{\xlongequal}}[2][]{{\mathrel{{\overset{{#2}}{{=}}}}}}
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
        .replace(
            "%__PAPER_META__%",
            f"满分：{paper.total_score:g} 分\\quad 本卷附参考答案与解析",
        )
        .replace("%__PAPER_BODY__%", "\n\n".join(body))
    )


def _section_description(paper: PaperPreviewRead, question_type: str) -> str:
    title = _section_title(paper, question_type, 1)
    return title.split("、", 1)[1]


def _teacher_answer_latex(item) -> list[str]:
    """Render the project's single reference-solution contract.

    OCR-published questions historically store the entire reviewed reference
    solution as one ``solution_steps`` element and often leave ``final_answer``
    empty.  Do not fabricate an independent answer row in that case.  Render
    the reviewed solution block directly, while still supporting older records
    that truly have a separate ``final_answer``.
    """
    result = ["\\begin{TeacherAnswer}", "\\textbf{参考解答：}\\par"]
    steps = [cleaned for step in item.solution_steps if (cleaned := _strip_solution_prefix(step))]

    if steps:
        if len(steps) == 1:
            # The OCR path intentionally stores one complete reviewed solution
            # block. Preserve its reviewed line boundaries: joining every OCR
            # line into one paragraph can turn several formulas into one
            # unbreakable horizontal box and cause right-margin overflow.
            result.append(_reference_solution_latex(steps[0]))
        else:
            result.append(
                "\\begin{enumerate}[label=\\arabic*.,leftmargin=2em,topsep=0.2em,itemsep=0.2em]"
            )
            result.extend(
                f"\\item {_mixed_latex(format_question_display(step))}" for step in steps
            )
            result.append("\\end{enumerate}")
    elif item.final_answer:
        result.append(_mixed_latex(item.final_answer) + "\\par")
    else:
        result.append("暂无参考解答。\\par")

    if item.knowledge:
        knowledge = _latex_text("、".join(item.knowledge))
        result.append(f"\\noindent\\textcolor{{slate}}{{知识点：{knowledge}}}\\par")
    result.extend(["\\end{TeacherAnswer}", "\\vspace{0.5cm}"])
    return result


def _strip_solution_prefix(step: str) -> str:
    """Remove one legacy wrapper label only when it occurs at the step start."""
    return re.sub(r"^\s*(?:参考解答|解析|解|证明)\s*[:：]\s*", "", step, count=1).strip()


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

# Arabic sub-questions retain the conservative historical rule: only split at
# the beginning of a line or after strong delimiters.  Roman-numbered exercise
# lists such as (I)...(VIII) are common in calculus OCR and are safe to split
# wherever they occur outside an explicit math environment.
_SUBQUESTION_LABEL = r"(?:\d+|[IVXivx]{1,6})"
_SUBQUESTION_PATTERN = re.compile(rf"^\(({_SUBQUESTION_LABEL})\)\s*(.*)$")
_ARABIC_SUBQUESTION_INLINE_PATTERN = re.compile(r"(?:^|(?<=[：:；;]))[ \t]*\((\d+)\)")
_ROMAN_SUBQUESTION_INLINE_PATTERN = re.compile(r"(?<![A-Za-z0-9])\(([IVXivx]{1,6})\)\s*")
_BLANK_TOKEN = "§EXAM_BLANK§"

# Historical OCR records contain mathematically valid TeX without $...$ / \(...\)
# delimiters.  Only a conservative command set is executed; unknown commands
# remain escaped text rather than being allowed to break the whole PDF compile.
_ALLOWED_MATH_COMMANDS = frozenset(
    {
        "frac", "dfrac", "tfrac", "sqrt", "int", "iint", "iiint", "oint",
        "sum", "prod", "lim", "sin", "cos", "tan", "cot", "sec", "csc",
        "ln", "log", "exp", "min", "max", "sup", "inf",
        "mathrm", "mathbf", "boldsymbol", "mathbb", "mathcal", "mathit",
        "operatorname", "text", "textstyle", "displaystyle", "scriptstyle",
        "left", "right", "lvert", "rvert", "lVert", "rVert",
        "overline", "underline", "hat", "widehat", "bar", "vec", "dot", "ddot",
        "partial", "nabla", "infty", "to", "rightarrow", "leftarrow",
        "Rightarrow", "Leftarrow", "Leftrightarrow", "mapsto",
        "xrightarrow", "xleftarrow", "xlongequal",
        "in", "notin", "subset", "subseteq", "supset", "supseteq",
        "cup", "cap", "emptyset", "forall", "exists",
        "le", "leq", "ge", "geq", "neq", "ne", "equiv", "approx", "sim",
        "pm", "mp", "cdot", "times", "div", "circ", "bullet",
        "quad", "qquad", "hspace", "vspace",
        "begin", "end", "overset", "underset", "stackrel", "prime", "limits",
        "Big", "big", "Bigg", "bigg", "Bigl", "Bigr", "bigl", "bigr",
        "Biggl", "Biggr", "biggl", "biggr", "middle",
        "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta",
        "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu",
        "xi", "pi", "varpi", "rho", "varrho", "sigma", "varsigma", "tau",
        "upsilon", "phi", "varphi", "chi", "psi", "omega",
        "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon",
        "Phi", "Psi", "Omega",
    }
)
_TEX_COMMAND_RE = re.compile(r"\\([A-Za-z]+)")
_TEXT_RUN_RE = re.compile(r"([\u4e00-\u9fff，。；：！？、（）【】《》“”‘’]+)")
_MATHISH_RE = re.compile(
    r"(?:\\[A-Za-z]+|[_^]\s*\{|[=<>]|→|∞|∑|∫|√|±|≤|≥|≠|≈|"
    r"(?:^|[\s(])(?:lim|sin|cos|tan|cot|ln|log)\b)"
)


def _question_parts(value: str) -> tuple[str, list[str]]:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    options = [line for line in lines if _OPTION_PATTERN.match(line)]
    stem = "\n".join(line for line in lines if not _OPTION_PATTERN.match(line))
    return stem, options


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
        for chunk in chunks:
            match = _SUBQUESTION_PATTERN.match(chunk)
            if match:
                flush_ordinary()
                label, content = match.groups()
                formatted.append(f"({label}) {_replace_blanks(content)}")
            elif chunk.strip():
                ordinary.append(_replace_blanks(chunk.strip()))
    flush_ordinary()
    return "\n".join(formatted)


def _split_subquestion_line(line: str) -> list[str]:
    """Split explicit sub-question markers outside protected math segments."""
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

    markers: list[tuple[int, int, str]] = []
    for match in _ARABIC_SUBQUESTION_INLINE_PATTERN.finditer(safe_line):
        markers.append((match.start(), match.end(), match.group(1)))
    for match in _ROMAN_SUBQUESTION_INLINE_PATTERN.finditer(safe_line):
        markers.append((match.start(), match.end(), match.group(1)))
    markers.sort(key=lambda item: (item[0], item[1]))

    # Deduplicate the same marker if two recognizers ever overlap.
    unique_markers: list[tuple[int, int, str]] = []
    for marker in markers:
        if unique_markers and marker[0] == unique_markers[-1][0]:
            continue
        unique_markers.append(marker)
    markers = unique_markers

    if not markers:
        return [line]

    chunks: list[str] = [safe_line[: markers[0][0]]]
    for index, marker in enumerate(markers):
        end = markers[index + 1][0] if index + 1 < len(markers) else len(safe_line)
        chunks.append(f"({marker[2]}) {safe_line[marker[1] : end]}")

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



def _reference_solution_latex(value: str) -> str:
    r"""Render one reviewed reference-solution block without flattening lines.

    Existing OCR questions store the entire ``## 参考解答`` section as one
    solution string. Source newlines are useful structural evidence, but a
    display-math block may itself span several source lines::

        $$
        y = \int_0^t ...
        $$

    Such a block must be consumed as one unit before normal line processing.
    Otherwise the standalone ``$$`` lines would become ``$$\\par`` and the
    formula line could be wrapped again in ``\\(...\\)``, producing invalid
    nested math environments.
    """
    value = unescape(value)
    lines = value.splitlines()
    rendered: list[str] = []

    index = 0
    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.strip()

        if not line:
            index += 1
            continue

        display = _consume_multiline_display_math(lines, index)
        if display is not None:
            display_latex, next_index = display
            rendered.append(display_latex)
            index = next_index
            continue

        rendered.extend(_reference_solution_line_latex(line))
        index += 1

    return "\n".join(rendered)


def _consume_multiline_display_math(
    lines: list[str],
    start_index: int,
) -> tuple[str, int] | None:
    r"""Consume a display-math block whose delimiters occupy separate lines.

    Supported forms:

    ``$$`` ... ``$$``
    ``\\[`` ... ``\\]``

    Same-line math such as ``$$x^2$$`` is intentionally left to the existing
    mixed-LaTeX path.
    """
    opening = lines[start_index].strip()

    if opening == "$$":
        closing = "$$"
    elif opening == r"\[":
        closing = r"\]"
    else:
        return None

    body: list[str] = []
    index = start_index + 1

    while index < len(lines):
        current = lines[index].strip()
        if current == closing:
            math = "\n".join(body).strip()
            if not math:
                # An empty display block is useless and can make later TeX
                # diagnostics harder to interpret. Drop it deterministically.
                return "", index + 1

            normalized = _normalize_legacy_tex(math)
            return f"\\[\n{normalized}\n\\]", index + 1

        body.append(lines[index])
        index += 1

    # Unmatched OCR delimiter: do not emit raw ``$$`` / ``\\[`` because that
    # would create invalid TeX. Let the ordinary text path escape it instead.
    return None


def _reference_solution_line_latex(line: str) -> list[str]:
    """Render one non-display source line from a reviewed solution."""
    rendered: list[str] = []

    for chunk in _split_subquestion_line(line):
        chunk = chunk.strip()
        if not chunk:
            continue

        match = _SUBQUESTION_PATTERN.match(chunk)
        if match:
            label, content = match.groups()
            rendered.append(
                f"\\ExamSubQuestion{{{label}}}"
                f"{{{_mixed_latex_inline(_replace_blanks(content))}}}"
            )
        else:
            rendered.append(
                _mixed_latex_inline(_replace_blanks(chunk)) + r"\par"
            )

    return rendered

def _mixed_latex(value: str) -> str:
    """Escape prose while preserving explicit and legacy OCR math segments."""
    value = unescape(value)
    rendered_lines: list[str] = []
    for line in value.splitlines():
        match = _SUBQUESTION_PATTERN.match(line.strip())
        if match:
            label, content = match.groups()
            rendered_lines.append(
                f"\\ExamSubQuestion{{{label}}}{{{_mixed_latex_inline(content)}}}"
            )
        else:
            rendered_lines.append(_mixed_latex_inline(line))
    return "\n".join(rendered_lines)


def _mixed_latex_inline(value: str) -> str:
    parts = _MATH_PATTERN.split(value)
    rendered: list[str] = []
    for part in parts:
        if _MATH_PATTERN.fullmatch(part):
            rendered.append(_normalize_legacy_tex(part))
            continue
        prose_parts = part.split(_BLANK_TOKEN)
        rendered.append(
            r"\underline{\hspace{2cm}}".join(
                _render_legacy_math_in_prose(piece) for piece in prose_parts
            )
        )
    return "".join(rendered)


def _render_legacy_math_in_prose(value: str) -> str:
    """Render historical bare TeX without treating arbitrary prose as code.

    MinerU/OCR history contains lines such as ``y = \\int_0^t ...`` without
    explicit math delimiters.  Chinese prose naturally separates most of those
    formula islands, so split by CJK runs and only enter math mode for non-CJK
    chunks that are demonstrably mathematical and use a known command set.
    """
    segments = _TEXT_RUN_RE.split(value)
    rendered: list[str] = []
    for segment in segments:
        if not segment:
            continue
        if _TEXT_RUN_RE.fullmatch(segment):
            rendered.append(_latex_text(segment))
            continue
        normalized = _normalize_legacy_tex(segment)
        if _looks_like_legacy_math(normalized) and _legacy_math_commands_are_safe(normalized):
            leading = normalized[: len(normalized) - len(normalized.lstrip())]
            trailing = normalized[len(normalized.rstrip()) :]
            core = normalized.strip()
            if core:
                rendered.append(_latex_text(leading))
                rendered.append(r"\(" + core + r"\)")
                rendered.append(_latex_text(trailing))
                continue
        rendered.append(_latex_text(segment))
    return "".join(rendered)


def _looks_like_legacy_math(value: str) -> bool:
    return bool(_MATHISH_RE.search(value))


def _legacy_math_commands_are_safe(value: str) -> bool:
    return all(command in _ALLOWED_MATH_COMMANDS for command in _TEX_COMMAND_RE.findall(value))


def _normalize_legacy_tex(value: str) -> str:
    """Repair only deterministic OCR spacing/command concatenation artifacts."""
    value = re.sub(r"\\lef\s+t\b", r"\\left", value)
    value = re.sub(r"\\righ\s+t\b", r"\\right", value)
    # OCR occasionally concatenates a one-letter argument onto standard
    # operator names: ``\\sint`` -> ``\\sin t``, ``\\toa`` -> ``\\to a``.
    value = re.sub(
        r"\\(sin|cos|tan|cot|sec|csc)([A-Za-z])\b",
        lambda match: f"\\{match.group(1)} {match.group(2)}",
        value,
    )
    value = re.sub(
        r"\\to([A-Za-z])\b",
        lambda match: f"\\to {match.group(1)}",
        value,
    )
    return value


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

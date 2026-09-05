"""二级子题拆分：题目与参考解答按相同子题编号同步拆分。

聚焦修复一个真实问题：父题拆成 1(1)…1(6) 后，每个子题的 ``## 参考解答``
之前被错误地填成了父题 ``(1)～(6)`` 的全部答案。

这些测试只覆盖「答案随子题同步拆分」逻辑，不触碰一级大题边界识别、
跨页 resplit / pending / preamble、Markdown schema 的其它部分、QuestionBank、发布状态机。
"""


from calculus_agent.workbench.ocr import (
    PlacedCandidate,
    QuestionCandidate,
    _split_answer_for_subquestions,
    _split_independent_candidates,
    render_drafts,
)
from calculus_agent.workbench.markdown_schema import parse_markdown


def _candidate(body: str, answer: str = "", analysis: str = "") -> QuestionCandidate:
    return QuestionCandidate(
        original_number="1",
        question_type="calculation",
        body=body,
        options={},
        answer=answer,
        analysis=analysis,
    )


# 父题干：计算下列极限，含 (1)(2)(3) 三个子题
PARENT_BODY = (
    "计算下列极限：\n"
    "(1)\n$$\\lim_{x\\to0}\\frac{\\sin\\omega x}{x};$$\n"
    "(2)\n$$\\lim_{x\\to0}\\frac{\\tan3x}{x};$$\n"
    "(3)\n$$\\lim_{x\\to0}\\frac{\\sin2x}{\\sin5x};$$"
)


def test_subanswer_one_to_one_correspondence():
    """题目 (1)-(3) 与答案 (1)-(3) 正常一一对应。"""
    answer = (
        "解(1)当$\\omega\\ne0$ 时，\n"
        "$$\\lim_{x\\to0}\\frac{\\sin\\omega x}{x}=\\omega;$$\n"
        "(2)\n$$\\lim_{x\\to0}\\frac{\\tan3x}{x}=3$$\n"
        "(3)\n$$\\lim_{x\\to0}\\frac{\\sin2x}{\\sin5x}=\\frac{2}{5}$$"
    )
    children = _split_independent_candidates(_candidate(PARENT_BODY, answer=answer))

    assert [c.original_number for c in children] == ["1(1)", "1(2)", "1(3)"]
    # 每个子题只拿到对应编号的答案，绝不混入其它子题
    assert "当" in children[0].answer and "\\omega" in children[0].answer
    assert "tan3x" in children[1].answer
    assert "sin5x" in children[2].answer
    # 子答案里不应出现其它子题的编号标记
    assert "(2)" not in children[0].answer
    assert "(3)" not in children[0].answer
    assert "(1)" not in children[1].answer
    assert "(3)" not in children[1].answer
    # 可靠的拆分不需要人工核对
    assert all(not c.needs_review for c in children)
    assert all(c.review_note == "" for c in children)


def test_subanswer_analysis_prefix_is_stripped():
    """答案前的「解析：」公共前缀被剥离，不重复进入每个子题答案。"""
    answer = (
        "解析：\n"
        "(1) 原式 = 1；\n"
        "(2) 原式 = 1/2；\n"
        "(3) 原式 = e。"
    )
    children = _split_independent_candidates(_candidate(PARENT_BODY, answer=answer))

    assert len(children) == 3
    for child in children:
        assert "解析：" not in child.answer
        assert "解析" not in child.answer.split("\n")[0]
    assert "原式 = 1" in children[0].answer
    assert "原式 = 1/2" in children[1].answer
    assert "原式 = e" in children[2].answer
    assert all(not c.needs_review for c in children)


def test_subanswer_latex_and_multiline_formula():
    """答案包含 LaTeX 块级公式与多行内容时，子答案完整保留。"""
    answer = (
        "解(1)\n$$\\lim_{x\\to0}\\frac{\\sin\\omega x}{x}\n=\\lim_{x\\to0}\\left(\\omega\\cdot\\frac{\\sin\\omega x}{\\omega x}\\right)\n=\\omega;$$\n"
        "(2)\n$$\\lim_{x\\to0}\\frac{\\tan3x}{x}=3$$\n"
        "(3)\n$$\\lim_{x\\to0}\\frac{\\sin2x}{\\sin5x}=\\frac{2}{5}$$"
    )
    children = _split_independent_candidates(_candidate(PARENT_BODY, answer=answer))

    # (1) 的答案跨越多行且含块级公式，必须完整捕获
    assert "$$\\lim" in children[0].answer
    assert "\\omega\\cdot" in children[0].answer
    assert "=\\omega;$$" in children[0].answer
    assert "(2)" not in children[0].answer


def test_subanswer_spanning_multiple_paragraphs():
    """某个子题答案跨多个段落时，整段（含空行）都被正确归属。"""
    answer = (
        "解(1)当$\\omega\\ne0$ 时，\n"
        "$$\\lim_{x\\to0}\\frac{\\sin\\omega x}{x}=\\omega;$$\n"
        "当$\\omega=0$ 时，\n"
        "$$\\lim_{x\\to0}\\frac{\\sin\\omega x}{x}=0=\\omega$$\n"
        "故不论ω为何值，均有$\\lim_{x\\to0}\\frac{\\sin\\omega x}{x}=\\omega$\n"
        "(2)\n$$\\lim_{x\\to0}\\frac{\\tan3x}{x}=3$$\n"
        "(3)\n$$\\lim_{x\\to0}\\frac{\\sin2x}{\\sin5x}=\\frac{2}{5}$$"
    )
    children = _split_independent_candidates(_candidate(PARENT_BODY, answer=answer))

    # (1) 的答案包含两段（ω≠0 与 ω=0）以及结论句
    assert "当$\\omega\\ne0$" in children[0].answer
    assert "当$\\omega=0$" in children[0].answer
    assert "故不论ω为何值" in children[0].answer
    # 不应混入 (2) 的答案
    assert "(2)" not in children[0].answer


def test_subanswer_unreliable_keeps_original_and_flags():
    """题目有子题但答案无法按编号拆分时：答案留空并标记需人工核对。"""
    answer = "解：由各题直接代入极限公式即可求得结果。"
    cand = _candidate(PARENT_BODY, answer=answer)
    children = _split_independent_candidates(cand)

    assert len(children) == 3
    for child in children:
        # 不强行错配：不得把父题完整答案复制给每个子题
        assert child.answer == ""
        assert child.needs_review is True
        assert child.review_note  # 非空审核提示
        assert child.match_status == "ambiguous"
    # 渲染后审核备注应承载该提示
    rendered = render_drafts(PlacedCandidate(page_number=1, candidate=cand))
    for r in rendered:
        parsed = parse_markdown(r.markdown)
        assert "参考解答" in parsed.sections
        assert "审核备注" in parsed.sections
        assert parsed.sections["审核备注"].strip() != ""


def test_subanswer_partial_markers_flags_unreliable():
    """答案只含 (1)(2) 但缺 (3) → 不可靠，不强行错配，标记需核对。"""
    answer = (
        "解(1) 原式 = 1；\n"
        "(2) 原式 = 1/2；"
    )
    cand = _candidate(PARENT_BODY, answer=answer)
    children = _split_independent_candidates(cand)

    assert len(children) == 3
    assert all(c.needs_review for c in children)
    # 已识别答案按编号分配，缺失的子题保持为空，不复制整段答案。
    assert children[0].answer == "原式 = 1；"
    assert children[1].answer == "原式 = 1/2；"
    assert children[2].answer == ""
    assert all(c.match_status == "ambiguous" for c in children)


def test_subanswer_without_parent_answer_is_missing_answer():
    children = _split_independent_candidates(_candidate(PARENT_BODY))
    assert all(c.match_status == "missing_answer" for c in children)
    assert all(c.answer == "" and c.analysis == "" for c in children)


def test_subanswer_reliable_answer_is_matched():
    answer = "(1) 答一\n(2) 答二\n(3) 答三"
    children = _split_independent_candidates(_candidate(PARENT_BODY, answer=answer))
    assert all(c.match_status == "matched" for c in children)


def test_numbered_trailing_note_does_not_trigger_duplicate_answer_round():
    body = "求下列极限：\n(1) a\n(2) b\n(3) c"
    analysis = "(1) 答一\n(2) 答二\n(3) 答三\n\n注本题采用：\n(1) 极限法则；\n(2) 等价无穷小。"
    children = _split_independent_candidates(_candidate(body, analysis=analysis))
    assert [child.analysis.splitlines()[0] for child in children] == ["答一", "答二", "答三"]
    assert all("注本题采用" in child.analysis for child in children)
    assert not any(child.needs_review for child in children)


def test_split_answer_for_subquestions_helper():
    """直接验证辅助函数的可靠/不可靠判定。"""
    # 可靠：所有子题编号都能对应
    text = "解(1) a\n(2) b\n(3) c"
    segs, flag = _split_answer_for_subquestions(text, ["1", "2", "3"])
    assert flag is False
    assert segs == {"1": "a", "2": "b", "3": "c"}

    # 不可靠：答案无编号
    segs, flag = _split_answer_for_subquestions("整体一段答案", ["1", "2"])
    assert flag is True
    assert segs is None

    # 空答案：无需拆分，不报警
    segs, flag = _split_answer_for_subquestions("", ["1", "2"])
    assert flag is False
    assert segs is None


def test_render_drafts_reliable_no_review_note():
    """可靠拆分渲染出的 Markdown：参考解答只含本子题答案，审核备注保持为空。"""
    answer = (
        "解(1) a\n(2) b\n(3) c"
    )
    cand = _candidate(PARENT_BODY, answer=answer)
    rendered = render_drafts(PlacedCandidate(page_number=1, candidate=cand))
    assert len(rendered) == 3

    parsed3 = parse_markdown(rendered[2].markdown)
    assert "c" in parsed3.sections["参考解答"]
    assert "a" not in parsed3.sections["参考解答"]
    assert "b" not in parsed3.sections["参考解答"]
    # 可靠拆分不改变模板输出（审核备注为空，保持与旧版一致）
    assert parsed3.sections["审核备注"].strip() == ""


def test_realistic_badcase_six_subquestions():
    """贴近真实 bad case：父题 1 有 6 个子题，答案用 解(1)…(6) 格式。"""
    body = (
        "计算下列极限：\n"
        "(1)\n$$\\lim_{x\\to0}\\frac{\\sin\\omega x}{x};$$\n"
        "(2)\n$$\\lim_{x\\to0}\\frac{\\tan3x}{x};$$\n"
        "(3)\n$$\\lim_{x\\to0}\\frac{\\sin2x}{\\sin5x};$$\n"
        "(4)\n$$\\lim_{x\\to0}x\\cot x;$$\n"
        "(5)\n$$\\lim_{x\\to0}\\frac{1-\\cos2x}{x\\sin x};$$\n"
        "(6)$\\lim_{n\\to\\infty}2^n\\sin\\frac{x}{2^n}$ (x为不等于零的常数)."
    )
    answer = (
        "解(1)当$\\omega\\ne0$ 时，\n"
        "$$\\lim_{x\\to0}\\frac{\\sin\\omega x}{x}=\\omega;$$\n"
        "当$\\omega=0$ 时，\n"
        "$$\\lim_{x\\to0}\\frac{\\sin\\omega x}{x}=0=\\omega$$\n"
        "故不论ω为何值，均有$\\lim_{x\\to0}\\frac{\\sin\\omega x}{x}=\\omega$$\n"
        "(2)\n$$\\lim_{x\\to0}\\frac{\\tan3x}{x}=3$$\n"
        "(3)\n$$\\lim_{x\\to0}\\frac{\\sin2x}{\\sin5x}=\\frac{2}{5}$$\n"
        "(4)\n$$\\lim_{x\\to0}x\\cot x=1.$$\n"
        "(5)\n$$\\lim_{x\\to0}\\frac{1-\\cos2x}{x\\sin x}=2$$\n"
        "(6)\n$$\\lim_{n\\to\\infty}2^n\\sin\\frac{x}{2^n}=x.$$"
    )
    children = _split_independent_candidates(_candidate(body, answer=answer))
    assert [c.original_number for c in children] == [
        "1(1)", "1(2)", "1(3)", "1(4)", "1(5)", "1(6)"
    ]
    # 关键回归：1(3) 的参考解答只剩 (3) 的答案，不再包含 (1)(2)(4)(5)(6)
    q3 = children[2]
    assert "sin5x" in q3.answer
    assert "(1)" not in q3.answer
    assert "(2)" not in q3.answer
    assert "(4)" not in q3.answer
    assert "(6)" not in q3.answer
    # 1(4) 的参考解答只剩 (4)
    assert "x\\cot x" in children[3].answer
    assert "(3)" not in children[3].answer
    assert all(not c.needs_review for c in children)


def test_subanswer_two_rounds_of_numbers_degrades():
    """答案里出现两轮重复 (1)-(4)（本父题答案后又拼接了另一道大题答案）：

    - 不自动拆答案；
    - 不发生 dict 覆盖（原始两段文本都保留）；
    - needs_review = True；
    - 有明确审核备注。
    """
    # 第一套 (1)(2)(3)(4) 是本题答案；第二套 (1)(2)(3)(4)(5) 是另一道大题的，
    # 被错误合并进同一个 candidate 的参考解答。关键点在于——
    # 第一套 (1)-(4) 的答案与第二套 (1)-(4) 的答案被写成重复编号时：
    dup_answer = (
        "解(1) 第一套答案一；\n"
        "(2) 第一套答案二；\n"
        "(3) 第一套答案三；\n"
        "(4) 第一套答案四；\n"
        "(1) 第二套答案一；\n"  # 重新从 (1) 开始 ⇒ 歧义
        "(2) 第二套答案二；\n"
        "(3) 第二套答案三；\n"
        "(4) 第二套答案四；\n"
        "(5) 第二套答案五。"
    )
    body = (
        "计算下列极限：\n"
        "(1) a\n(2) b\n(3) c\n(4) d"
    )
    children = _split_independent_candidates(_candidate(body, answer=dup_answer))

    assert len(children) == 4
    # 不自动拆：每个子题答案留空，避免制造错误对应关系
    for child in children:
        assert child.answer == ""
        assert child.needs_review is True
        assert "答案无法可靠拆分" in child.review_note


def test_subanswer_no_silent_text_loss_on_ambiguity():
    """歧义答案不得被伪装成任何子题的可靠答案。"""
    sentinel = "UNIQUE_SENTINEL_第2题专属解析_ABC123"
    dup_answer = (
        "解(1) 第一套答案一；\n"
        "(2) 第一套答案二；\n"
        "(3) 第一套答案三；\n"
        "(4) 第一套答案四；\n"
        f"(1) 第二套含{sentinel}；\n"
        "(2) 第二套答案二；\n"
        "(3) 第二套答案三；\n"
        "(4) 第二套答案四；\n"
        "(5) 第二套答案五。"
    )
    body = "计算下列极限：\n(1) a\n(2) b\n(3) c\n(4) d"
    cand = _candidate(body, answer=dup_answer)
    children = _split_independent_candidates(cand)

    assert all(child.answer == "" for child in children)
    assert all(child.needs_review for child in children)
    assert sentinel in cand.answer  # 原始确实有


def test_real_badcase_q3q4_text_not_lost_initial():
    """真实 bad case（初始错误 OCR）：

    第3、4题未被识别为独立题，但其唯一题干文本绝不能因「自动答案切分」
    而从所有 Draft 中消失——应保留在合并进第2题的参考解答里，并标记需复核。

    这里用与真实 fixture 同一机制的“两级子题 + 重复编号合并”构造最小复现：
    父题 #2 的计算小题答案后，拼接了另一道大题（含重复 (1)-(4) 编号）的答案。
    """
    sentinel_q3 = "UNIQUE_Q3_题干文本_极限存在准则"
    sentinel_q4 = "UNIQUE_Q4_题干文本_极限存在准则证明"
    # 模拟合并后的 analysis：Q2 自身答案 (1)-(4) + 混入的 Q3/Q4 文本（含重复编号）
    analysis = (
        "解(1) Q2答案一；\n"
        "(2) Q2答案二；\n"
        "(3) Q2答案三；\n"
        "(4) Q2答案四；\n"
        f"(1) {sentinel_q3}；\n"
        f"(2) {sentinel_q4}；\n"
        "(3) 混入答案三；\n"
        "(4) 混入答案四；\n"
        "(5) 混入答案五。"
    )
    body = "计算下列极限：\n(1) a\n(2) b\n(3) c\n(4) d"
    cand = _candidate(body, answer="", analysis=analysis)
    children = _split_independent_candidates(cand)

    # 降级：不把父题完整 analysis 复制给子题；全部留空并明确标记
    assert all(child.analysis == "" for child in children)
    assert all(child.needs_review for child in children)
    assert all("答案无法可靠拆分" in child.review_note for child in children)


def test_real_badcase_preserved_after_resplit_is_no_worse():
    """端到端：基于真实 fixture 的初始导入，断言 Q3/Q4 唯一题干文本存在于
    至少一个最终 Draft（未被自动答案切分删除），且不在任何「题目内容」段。

    这对应“初始错误 OCR 下，第3/4题可以不是独立题，但原始唯一文本不能被
    自动答案切分删除”的要求。修复后 Q3/Q4 文本应保留在 #2 的参考解答中。
    """

    from calculus_agent.workbench.ocr import split_pages_into_candidates, render_drafts

    from pathlib import Path

    FIX = Path(__file__).parent / "fixtures" / "ocr"
    pages = {
        n: (FIX / f"badcase_src_12132b6b_page_{n:04d}.md").read_text(encoding="utf-8")
        for n in (1, 2, 3)
    }
    placed = split_pages_into_candidates([(n, pages[n]) for n in (1, 2, 3)])

    all_md = []
    for p in placed:
        for d in render_drafts(p):
            all_md.append(d.markdown)
    joined = "\n".join(all_md)

    # 原始 OCR 中明确存在的文本仍可追溯；本轮只禁止把父题完整答案复制成
    # 每个子题的答案/解析，不能据此把原始题干文本从 Draft 中抹掉。
    assert "证明极限存在的准则Ⅰ" in joined
    assert "利用极限存在准则证明" in joined


def test_real_badcase_fixed_page2_resplit_puts_q3q4_in_own_body():
    """端到端：用户修正 Page 2（把被毁的 3、4 题号修好）后重新切题，Q3/Q4
    唯一题干文本必须进入各自「题目内容」，并彻底离开第2题。
    """
    from pathlib import Path

    from calculus_agent.workbench.ocr import split_pages_into_candidates, render_drafts
    from calculus_agent.workbench.markdown_schema import parse_markdown

    FIX = Path(__file__).parent / "fixtures" / "ocr"
    pages = {
        n: (FIX / f"badcase_src_12132b6b_page_{n:04d}.md").read_text(encoding="utf-8")
        for n in (1, 2, 3)
    }
    # 模拟用户修正 Page 2
    pages[2] = pages[2].replace(
        "$ 得 ^*3$ ·a 的定义，证明极限存在的准则Ⅰ",
        "3.根据函数极限的定义，证明极限存在的准则Ⅰ",
    ).replace(
        "河4.利用极限存在准则证明：",
        "4.利用极限存在准则证明：",
    )
    placed = split_pages_into_candidates([(n, pages[n]) for n in (1, 2, 3)])

    q3_body = q4_body = None
    q2_body = ""
    for p in placed:
        for d in render_drafts(p):
            parsed = parse_markdown(d.markdown)
            body_text = parsed.sections.get("题目内容", "")
            if d.original_number == "3" and p.page_number == 2:
                q3_body = body_text
            if d.original_number.startswith("4") and p.page_number == 2:
                q4_body = body_text
            if d.original_number == "2" and p.page_number == 1:
                q2_body += body_text

    assert q3_body is not None, "修正后必须切出第3题"
    assert q4_body is not None, "修正后必须切出第4题"
    assert "证明极限存在的准则Ⅰ" in q3_body
    assert "利用极限存在准则证明" in q4_body
    # 彻底离开第2题题目内容
    assert "证明极限存在的准则Ⅰ" not in q2_body
    assert "利用极限存在准则证明" not in q2_body

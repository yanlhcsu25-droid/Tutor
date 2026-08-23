from calculus_agent.workbench.import_pipeline import (
    DocumentLayout,
    import_document,
    normalize_match_number,
)
from calculus_agent.workbench.ocr import (
    _selected_pages_for_layout,
    split_pages_into_candidates,
)
from calculus_agent.workbench.ocr import RawQuestion, _candidate_from_raw, split_candidate_subquestions


def test_separate_layout_only_selects_question_and_solution_pages_for_ocr():
    layout = DocumentLayout("separate", [1, 2, 5], [8, 9])
    assert _selected_pages_for_layout(layout) == [1, 2, 5, 8, 9]
    assert _selected_pages_for_layout(DocumentLayout("inline")) == []


def test_parenthesized_numbers_are_top_level_in_explicit_choice_section():
    pages = [
        (1, "## 一、选择题\n\n(1) 第一题\nA.甲 B.乙\n\n(2) 第二题\nA.甲 B.乙"),
        (2, "## 一、选择题\n\n(1) 答案：A\n解 第一题解析\n\n(2) 答案：B\n解 第二题解析"),
    ]
    result = import_document(pages, DocumentLayout("separate", [1], [2]))
    candidates = {item.candidate.original_number: item.candidate for item in result.candidates}
    assert list(candidates) == ["1", "2"]
    assert candidates["1"].answer.startswith("A")
    assert candidates["2"].answer.startswith("B")


def test_parenthesized_numbers_remain_subquestions_without_objective_heading():
    candidates = split_pages_into_candidates([(1, "求下列各式：\n(1) a\n(2) b")])
    assert candidates == []


def _by_number(result):
    return {item.candidate.original_number: item.candidate for item in result.candidates}


def test_inline_layout_keeps_existing_candidate_behavior():
    pages = [(1, "1. 求极限\n答案：1\n\n2. 证明命题\n解：成立")]
    old = split_pages_into_candidates(pages)
    new = import_document(pages, DocumentLayout()).candidates
    assert new == old


def test_separate_paper_matches_questions_and_solutions():
    pages = [
        (1, "1. 求极限\n\n2. 计算导数\n\n3. 证明命题"),
        (2, "1. 答案：1\n\n2. 答案：2x\n\n3. 解：由定义可证"),
    ]
    result = import_document(pages, DocumentLayout("separate", [1], [2]))
    candidates = _by_number(result)
    assert candidates["1"].answer == "1"
    assert candidates["2"].answer == "2x"
    assert "由定义可证" in candidates["3"].analysis
    assert result.diagnostics.unmatched_solutions == []


def test_separate_answers_infer_unlabeled_first_answer_from_following_numbered_answer():
    result = import_document(
        [(1, "1. 题目一\n\n2. 题目二"),
         (2, "第一题解析正文\n\n2.D【解析】第二题解析")],
        DocumentLayout("separate", [1], [2]),
    )
    candidates = _by_number(result)
    assert candidates["1"].answer == "第一题解析正文"
    assert candidates["2"].answer.startswith("D")
    assert result.diagnostics.missing_questions == []


def test_separate_subquestions_match_one_to_one_and_normalize_parentheses():
    pages = [
        (1, "4. 求下列极限：\n（1）第一式\n（2）第二式\n（3）第三式"),
        (2, "4. 解（1）答案甲\n（2）答案乙\n（3）答案丙"),
    ]
    result = import_document(pages, DocumentLayout("separate", [1], [2]))
    candidates = _by_number(result)
    assert list(candidates) == ["4(1)", "4(2)", "4(3)"]
    assert candidates["4(1)"].answer == "甲"
    assert candidates["4(2)"].answer == "乙"
    assert candidates["4(3)"].answer == "丙"


def test_missing_answer_keeps_question_for_review():
    pages = [(1, "1. 问题一\n\n2. 问题二"), (2, "1. 答案：甲")]
    result = import_document(pages, DocumentLayout("separate", [1], [2]))
    missing = _by_number(result)["2"]
    assert missing.answer == ""
    assert missing.needs_review is True
    assert "未找到" in missing.review_note


def test_extra_and_duplicate_solutions_are_never_silently_overwritten():
    pages = [
        (1, "1. 问题一"),
        (2, "1. 答案：甲\n\n1. 答案：乙\n\n7. 答案：多余"),
    ]
    result = import_document(pages, DocumentLayout("separate", [1], [2]))
    question = result.candidates[0].candidate
    assert question.answer == ""
    assert question.needs_review is True
    assert [item.key[1] for item in result.diagnostics.unmatched_solutions] == ["1", "1", "7"]


def test_repeated_numbers_in_explicit_sections_match_by_section():
    pages = [
        (1, "# 一、选择题\n1. 第一题\n# 二、填空题\n1. 第二题"),
        (2, "# 一、选择题\n1. 答案：A\n# 二、填空题\n1. 答案：B"),
    ]
    result = import_document(pages, DocumentLayout("separate", [1], [2]))
    assert [item.candidate.answer for item in result.candidates] == ["A", "B"]


def test_solution_continuation_is_merged_across_pages():
    pages = [
        (1, "1. 问题一"),
        (2, "1. 解：第一步"),
        (3, "第二步\n\n2. 解：多余答案"),
    ]
    result = import_document(pages, DocumentLayout("separate", [1], [2, 3]))
    assert "第一步" in result.candidates[0].candidate.analysis
    assert "第二步" in result.candidates[0].candidate.analysis
    assert [item.key[1] for item in result.diagnostics.unmatched_solutions] == ["2"]


def test_number_key_normalizes_major_and_subquestion_forms():
    assert normalize_match_number("1.") == "1"
    assert normalize_match_number("第1题") == "1"
    assert normalize_match_number("3.(1)") == "3-1"
    assert normalize_match_number("3（1）") == "3-1"
    assert normalize_match_number("第3题第1问") == "3-1"


def test_separate_layout_uses_question_and_answer_page_ranges():
    pages = [
        (1, "1. 第一题"), (2, "2. 第二题"),
        (3, "答案页前言"), (4, "1. 答案：甲\n2. 答案：乙"),
    ]
    result = import_document(pages, DocumentLayout("separate", [1, 2], [4]))
    assert [item.candidate.answer for item in result.candidates] == ["甲", "乙"]
    assert all(item.candidate.match_method == "exact_number" for item in result.candidates)


def test_unreliable_parent_solution_never_copies_analysis_to_children():
    parent = _candidate_from_raw(RawQuestion(
        "6", "求下列各式：\n(1) a\n(2) b\n(3) c\n解：完整父题解析，没有可靠子题编号。", "calculation"
    ))
    assert parent is not None
    children = split_candidate_subquestions(parent)
    assert len(children) == 3
    assert all(child.analysis == "" for child in children)
    assert all(child.needs_review for child in children)
    assert all("答案无法可靠拆分" in child.review_note for child in children)


def test_missing_or_duplicate_subquestion_numbers_are_exposed():
    body = "求下列各式：\n(1)\n(2)\n(3) a\n(3) a重复\n(4) b\n(5) c"
    parent = _candidate_from_raw(RawQuestion("6", body, "calculation"))
    assert parent is not None
    children = split_candidate_subquestions(parent)
    assert children
    notes = "\n".join(child.review_note for child in children)
    assert "缺少可构造正文的编号：1,2" in notes
    assert "重复子题编号：3" in notes
    assert all(child.needs_review for child in children)


def test_solution_boundary_accepts_standalone_marker_after_sentence_punctuation():
    candidate = _candidate_from_raw(RawQuestion("5", "求导数。解：\n先求导，再化简。", "calculation"))
    assert candidate is not None
    assert candidate.body == "求导数。"
    assert candidate.analysis == "先求导，再化简。"


def test_chinese_prefixed_solution_numbering_ignores_body_numbered_lists():
    result = import_document(
        [(1, "1. 第一题\n2. 第二题"), (2, "第1题\n1. 因为……\n2. 所以……\n\n第2题\n1. 由定义可得")],
        DocumentLayout("separate", [1], [2]),
    )
    assert len(result.diagnostics.unmatched_solutions) == 0
    assert [item.candidate.analysis for item in result.candidates] == ["", ""]
    assert [item.candidate.answer for item in result.candidates] == [
        "1. 因为……\n2. 所以……", "1. 由定义可得"
    ]

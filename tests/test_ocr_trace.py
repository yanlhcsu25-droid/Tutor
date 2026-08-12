from calculus_agent.workbench.ocr import (
    normalize_page,
    parse_question_sections,
    split_major_questions,
    trace_split_pages,
)
from pathlib import Path


def test_split_stages_preserve_cross_page_question_number():
    page_one = "1. 第一题题干\n\n2. 第二题题干的一部分"
    page_two = "（续）第二题剩余内容\n\n3. 第三题"

    normalized = normalize_page(page_one)
    preamble, chunks = split_major_questions(normalized)
    assert not preamble
    assert [item.original_number for item in chunks] == ["1", "2"]
    assert parse_question_sections(chunks[0]).body == "第一题题干"

    trace = trace_split_pages([(1, page_one), (2, page_two)])
    assert trace.pages[1]["has_continuation"] is True
    assert [item["original_number"] for item in trace.candidates] == ["1", "2", "3"]
    assert trace.candidates[1]["page_number"] == 1


def test_trace_reports_unrecognized_page():
    trace = trace_split_pages([(1, "这是 OCR 乱码，没有可识别题号")])
    assert trace.candidates == []
    assert any("未编号" in warning for warning in trace.warnings)


def test_trace_uses_fixed_cross_page_numbers():
    fixture_dir = Path(__file__).parent / "fixtures" / "ocr"
    pages = []
    for number in (1, 2, 3):
        text = (fixture_dir / f"badcase_src_12132b6b_page_{number:04d}.md").read_text()
        if number == 2:
            text = text.replace("$ 得 ^*3$ ·a 的定义，证明极限存在的准则Ⅰ", "3. 根据函数极限的定义，证明极限存在的准则Ⅰ")
            text = text.replace("河4.利用极限存在准则证明：", "4.利用极限存在准则证明：")
        pages.append((number, text))

    trace = trace_split_pages(pages)
    numbers = [item["original_number"] for item in trace.candidates]
    assert numbers[:4] == ["1", "2", "3", "4"]
    assert trace.candidates[2]["page_number"] == 2

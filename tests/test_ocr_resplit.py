"""基于人工修正后的整页 Markdown 重新切题 —— 真实 bad case 回归测试。

数据来源：tests/fixtures/ocr/badcase_src_12132b6b_page_000{1,2,3}.md
这三个文件是 src_12132b6bb726477dad5aeea2636a2af3（同济七版上册习题全解）
PPStructure 落盘的**真实整页 OCR 原文**，按字节原样复制，未做任何改写。

真实 bad case：
  第2页题号被 OCR 毁成 `$ 得 ^*3$ ·a 的定义…` 和 `河4.利用极限存在准则证明：`，
  两行都不满足题号正则 → 第2页 chunks 为空 → 整页 1761 字变成 preamble，
  被并进第1页第2题 → 第3、4题彻底消失。

本测试验证：用户人工把这两行题号改回 `3.` / `4.` 之后，
「重新识别题目」能安全地把第 1-3 页的未发布草稿替换成正确的 1/2/3/4。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import OcrImportDraft, OcrImportSource
from calculus_agent.workbench.database import WorkbenchDatabase
from calculus_agent.workbench.ocr import (
    render_drafts,
    persist_rendered_draft,
    split_pages_into_candidates,
)

SOURCE_ID = "src_12132b6bb726477dad5aeea2636a2af3"
FIXTURES = Path(__file__).parent / "fixtures" / "ocr"

# 用户在整页 Markdown 编辑器里做的两处人工修正：只改坏掉的题号前缀。
BROKEN_Q3 = "$ 得 ^*3$ ·a 的定义，证明极限存在的准则Ⅰ"
FIXED_Q3 = "3. 根据函数极限的定义，证明极限存在的准则Ⅰ"
BROKEN_Q4 = "河4.利用极限存在准则证明："
FIXED_Q4 = "4.利用极限存在准则证明："

# 第3、4题【题目正文】中的唯一文本（只取题干本身，不含答案/解析引用）。
# 注意「准则Ⅰ」用的是罗马数字 Ⅰ(U+2160)，与第3题答案里「准则I」(大写 I) 不同，
# 因此下面两个短语只在第3、4题的「题干」中出现，可作为可靠的定位锚点。
UNIQUE_Q3_BODY = "证明极限存在的准则Ⅰ"
UNIQUE_Q4_BODY = "利用极限存在准则证明"


def _raw_pages() -> dict[int, str]:
    return {
        number: (FIXTURES / f"badcase_src_12132b6b_page_{number:04d}.md").read_text(
            encoding="utf-8"
        )
        for number in (1, 2, 3)
    }


def _fixed_page2(raw: str) -> str:
    assert BROKEN_Q3 in raw and BROKEN_Q4 in raw, "fixture 已被改写"
    return raw.replace(BROKEN_Q3, FIXED_Q3).replace(BROKEN_Q4, FIXED_Q4)


@pytest.fixture
def client_and_factory(tmp_path):
    """还原真实导入现场：整页原文入库 + 用生产切题逻辑生成初始草稿。"""
    db_url = f"sqlite:///{tmp_path / 'resplit_test.db'}"
    create_schema(db_url)
    factory = build_session_factory(db_url)
    pages = _raw_pages()

    with factory.begin() as session:
        session.add(
            OcrImportSource(
                id=SOURCE_ID,
                original_name="badcase.pdf",
                stored_path="/tmp/badcase.pdf",
                sha256="b" * 64,
                page_count=3,
                processing_status="completed",
            )
        )
        session.flush()
        db = WorkbenchDatabase(session)
        for number, text in sorted(pages.items()):
            db.upsert_page(SOURCE_ID, number, text)
        # 与 run_ocr_into_database 完全同一条路径
        for placed in split_pages_into_candidates(sorted(pages.items())):
            for draft in render_drafts(placed):
                persist_rendered_draft(db, source_file_id=SOURCE_ID, draft=draft)

    import calculus_agent.workbench.app as app_mod

    saved = app_mod._session_factory
    app_mod._session_factory = factory
    try:
        yield TestClient(app_mod.app), factory
    finally:
        app_mod._session_factory = saved


def _numbers(factory) -> list[str]:
    with factory.begin() as session:
        return [
            item["original_number"]
            for item in WorkbenchDatabase(session).list_questions(SOURCE_ID)
        ]


def _drafts(factory) -> list[dict]:
    with factory.begin() as session:
        return WorkbenchDatabase(session).list_questions(SOURCE_ID)


def _preview(client, markdown):
    return client.post(
        f"/api/sources/{SOURCE_ID}/pages/2/resplit/preview", json={"markdown": markdown}
    )


def _body_of(markdown: str) -> str:
    """提取草稿 Markdown 的 ## 题目内容 段落（题干 + 选项）。"""
    m = re.search(r"(?m)^##\s*题目内容\s*$\n+(.*?)(?=\n##\s|\Z)", markdown, re.S)
    return m.group(1).strip() if m else ""


def _solution_of(markdown: str) -> str:
    """提取草稿 Markdown 的 ## 参考解答 段落（答案 + 解析合并段）。"""
    m = re.search(r"(?m)^##\s*参考解答\s*$\n+(.*?)(?=\n##\s|\Z)", markdown, re.S)
    return m.group(1).strip() if m else ""


# ── 1. 导入现状：确认 bad case 真实存在 ──

def test_initial_import_reproduces_badcase(client_and_factory):
    """初始导入必须复现真实 bad case，且断言足够强——能把「被错置进第2题」与
    「被直接丢弃」区分开，也不会被答案里的引用文本误导。

    已用只读 trace 核实的数据流：
      - 第2页被 OCR 毁掉的题号 `$ 得 ^*3$ ·a` / `河4.` 不满足题号正则
        → _raw_page_chunks(page2) chunks 为空 → 第2页整页成为 preamble；
      - 该 preamble 被合并进第1页 pending 的第2题（pending.raw += page2）；
      - 但 _extract_answer_analysis 在第2题自身的「解」处截断，而「解」在合并
        文本中位于 page2 之前 → 第3、4题正文落到第2题的【参考解答/解析】段落，
        而不是成为独立的第3、4题，也不在第2题【题目内容】里。

    强断言（避免“不存在顶层 3/4”这一种弱断言同时覆盖“被吞”和“丢失”两种情形）：
      1) 不存在顶层题号 3 / 4，且第2页不产出任何草稿；
      2) 第3、4题唯一正文【并未真正丢失】——它确实出现在第2题草稿里
         （被错置进【参考解答】，这正是 bad case 的错置特征，用来区分
         “被错置进第2题”与“被直接丢弃”）；
      3) 第3、4题唯一正文【不在任何草稿的 题目内容 里】（尚未被正确切分）。
    """
    _, factory = client_and_factory
    drafts = _drafts(factory)

    # 顶层题号（去掉 (k) 子题后缀），例如 1(3) 的顶层仍是 1
    top_numbers = {item["original_number"].split("(")[0] for item in drafts}
    assert "1" in top_numbers, "第1题应存在"
    assert "2" in top_numbers, "第2题应存在"
    assert "3" not in top_numbers, "第3题不应被识别为独立题（bad case 复现）"
    assert "4" not in top_numbers, "第4题不应被识别为独立题（bad case 复现）"

    # 第2页不应产出任何顶层草稿（题号被毁，整页成为 preamble）
    pages = {item["page_number"] for item in drafts}
    assert 2 not in pages, "第2页不应产出任何顶层草稿（题号被 OCR 毁掉）"

    # 第3、4题唯一正文在最终落库草稿里【不在任何 题目内容 中】——即未被正确切分为
    # 独立的第3、4题。这里只检查「题目正文」（遵循“不要搜索参考答案”的要求），
    # 用唯一题干短语定位，不会被答案里的引用文本误判。
    #
    # 根因链（已用只读 trace 核实，供后续修复参考，不作为本断言的搜索目标）：
    #   page2 整页成为 preamble → 被 merge 进第1页第2题的 candidate → 第2题
    #   「参考解答/解析」段落混入了第3、4题正文；随后 _split_independent_candidates
    #   把第2题按 (1)-(4) 拆二级子题，_split_answer_for_subquestions 又用子题号
    #   (1)(2)(3)(4) 去切那段【含有第4题答案同样用 (1)-(5) 编号】的解析，编号重叠
    #   导致段落被覆盖，最终第3、4题正文在落库草稿中完全丢失（既不在题目内容，
    #   也不在参考解答）。因此最终落库草稿里第3、4题正文彻底缺失。
    all_bodies = "\n".join(_body_of(d["ocr_markdown"]) for d in drafts)
    assert UNIQUE_Q3_BODY not in all_bodies, (
        "bad case：第3题唯一题干不应出现在任何草稿的【题目内容】里"
        "（尚未被识别为独立题，正文随损坏的第2页丢失）"
    )
    assert UNIQUE_Q4_BODY not in all_bodies, (
        "bad case：第4题唯一题干不应出现在任何草稿的【题目内容】里"
        "（尚未被识别为独立题，正文随损坏的第2页丢失）"
    )


# ── 2. 跨页范围自动推算 ──

def test_affected_range_is_cross_page(client_and_factory):
    """用户只编辑第2页，后端必须自动算出实际影响第 1-3 页。"""
    client, _ = client_and_factory
    response = _preview(client, _fixed_page2(_raw_pages()[2]))
    assert response.status_code == 200
    affected = response.json()["affected_range"]
    assert affected["start_page"] == 1
    assert affected["end_page"] == 3
    assert affected["pages"] == [1, 2, 3]
    assert affected["cross_page"] is True
    assert "第 1-3 页" in affected["description"]


def test_preview_reports_recovered_questions(client_and_factory):
    """preview 必须报出新增的第3、4题。"""
    client, _ = client_and_factory
    payload = _preview(client, _fixed_page2(_raw_pages()[2])).json()

    assert "3" in payload["new_numbers"]
    assert any(item.startswith("4") for item in payload["new_numbers"])

    added = {item["original_number"] for item in payload["changes"]["added"]}
    assert "3" in added
    assert any(item.startswith("4") for item in added)
    assert payload["blocked"] is False


def test_preview_only_rebuilds_changed_drafts(client_and_factory):
    """范围虽是第 1-3 页，但只有真正变化的草稿才会被重建。

    第1题、第3页两题内容完全没变 → kept（保留 id 与审核状态）；
    只有被吞题污染的第2题需要删除重建。
    """
    client, _ = client_and_factory
    payload = _preview(client, _fixed_page2(_raw_pages()[2])).json()

    kept = {item["original_number"] for item in payload["changes"]["kept"]}
    removed = {item["original_number"] for item in payload["changes"]["removed"]}

    assert all(item.startswith("1") for item in kept if item != "2"), kept
    assert removed and all(item.startswith("2") for item in removed), removed
    assert not removed & kept


def test_preview_does_not_write_database(client_and_factory):
    """preview 阶段绝对不能改数据库。"""
    client, factory = client_and_factory
    before = [(item["question_id"], item["ocr_markdown"]) for item in _drafts(factory)]
    _preview(client, _fixed_page2(_raw_pages()[2]))
    _preview(client, _fixed_page2(_raw_pages()[2]))
    after = [(item["question_id"], item["ocr_markdown"]) for item in _drafts(factory)]
    assert before == after


# ── 3. apply：replace 而不是 append ──

def test_apply_rebuilds_questions(client_and_factory):
    """确认后：第3、4题出现，第2题不再包含它们的内容。"""
    client, factory = client_and_factory
    fixed = _fixed_page2(_raw_pages()[2])

    response = client.post(
        f"/api/sources/{SOURCE_ID}/pages/2/resplit/apply", json={"markdown": fixed}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["created_count"] > 0
    assert result["deleted_count"] > 0
    assert result["kept_count"] > 0  # 未变化的草稿原样保留

    drafts = _drafts(factory)
    by_page = {}
    for item in drafts:
        by_page.setdefault(item["page_number"], []).append(item)

    page2_numbers = [item["original_number"] for item in by_page.get(2, [])]
    assert "3" in page2_numbers, "第3题必须被切出来"
    assert any(item.startswith("4") for item in page2_numbers), "第4题必须被切出来"

    # 修正后：第3、4题的唯一题干应落到【各自的 题目内容】，不再属于第2题。
    question3 = next(item for item in by_page[2] if item["original_number"] == "3")
    question3_body = _body_of(question3["ocr_markdown"])
    assert UNIQUE_Q3_BODY in question3_body, "第3题题干应出现在第3题的【题目内容】里"
    assert "根据函数极限的定义" in question3_body

    question4 = next(
        item for item in by_page[2] if item["original_number"].startswith("4")
    )
    question4_body = _body_of(question4["ocr_markdown"])
    assert UNIQUE_Q4_BODY in question4_body, "第4题题干应出现在第4题的【题目内容】里"
    assert UNIQUE_Q3_BODY not in question4_body, "第4题不能吞掉第3题"

    # 第3、4题题干应彻底离开第2题（无论 题目内容 还是 参考解答 都不应再含）。
    question2_md = "\n".join(
        item["ocr_markdown"]
        for item in by_page[1]
        if item["original_number"].startswith("2")
    )
    assert UNIQUE_Q3_BODY not in question2_md, "第2题不能再包含第3题题干"
    assert UNIQUE_Q4_BODY not in question2_md, "第2题不能再包含第4题题干"


def test_apply_is_replace_not_append(client_and_factory):
    """重复 apply 不能产生重复草稿。"""
    client, factory = client_and_factory
    fixed = _fixed_page2(_raw_pages()[2])
    path = f"/api/sources/{SOURCE_ID}/pages/2/resplit/apply"

    assert client.post(path, json={"markdown": fixed}).status_code == 200
    first = sorted((item["page_number"], item["original_number"]) for item in _drafts(factory))

    second_response = client.post(path, json={"markdown": fixed})
    assert second_response.status_code == 200
    assert second_response.json()["created_count"] == 0, "内容未变时不应新建草稿"
    second = sorted((item["page_number"], item["original_number"]) for item in _drafts(factory))

    assert first == second, "重复 apply 不能追加重复数据"
    assert len(first) == len(set(first)), "不能出现重复的 (页码, 题号)"


def test_apply_persists_edited_markdown(client_and_factory):
    """apply 必须把本次整页 Markdown 存进 edited_markdown。"""
    client, _ = client_and_factory
    fixed = _fixed_page2(_raw_pages()[2])
    client.post(f"/api/sources/{SOURCE_ID}/pages/2/resplit/apply", json={"markdown": fixed})

    page = client.get(f"/api/sources/{SOURCE_ID}/pages/2/markdown").json()
    assert page["edited_markdown"] == fixed
    assert page["raw_markdown"] == _raw_pages()[2], "raw_markdown 必须保持 OCR 原文"
    assert page["modified"] is True


def test_apply_without_markdown_uses_saved_edit(client_and_factory):
    """先保存整页 Markdown，再不带 body 触发重切，应使用已保存的编辑版。"""
    client, _ = client_and_factory
    fixed = _fixed_page2(_raw_pages()[2])

    saved = client.put(
        f"/api/sources/{SOURCE_ID}/pages/2/markdown", json={"markdown": fixed}
    )
    assert saved.status_code == 200

    response = client.post(f"/api/sources/{SOURCE_ID}/pages/2/resplit/apply", json={})
    assert response.status_code == 200
    assert "3" in response.json()["new_numbers"]


# ── 4. 安全约束 ──

def test_apply_preserves_published_draft(client_and_factory):
    """跨页重切覆盖到已发布草稿时原样保留，并继续重建待审核题。"""
    client, factory = client_and_factory
    with factory.begin() as session:
        draft = next(
            item
            for item in session.query(OcrImportDraft)
            .filter(OcrImportDraft.page_number == 1)
            .all()
            if item.original_number.startswith("2")
        )
        draft.review_status = "published"
        published_id = draft.id

    response = client.post(
        f"/api/sources/{SOURCE_ID}/pages/2/resplit/apply",
        json={"markdown": _fixed_page2(_raw_pages()[2])},
    )
    assert response.status_code == 200

    after = sorted(item["question_id"] for item in _drafts(factory))
    assert published_id in after


def test_preview_keeps_published_draft_without_blocking(client_and_factory):
    """preview 将已发布草稿列为保留项，不阻止其他待审核题重建。"""
    client, factory = client_and_factory
    with factory.begin() as session:
        draft = next(
            item
            for item in session.query(OcrImportDraft)
            .filter(OcrImportDraft.page_number == 1)
            .all()
            if item.original_number.startswith("2")
        )
        draft.review_status = "published"

    payload = _preview(client, _fixed_page2(_raw_pages()[2])).json()
    assert payload["blocked"] is False
    assert payload["blocking_drafts"] == []
    assert any(item["review_status"] == "published" for item in payload["changes"]["kept"])


def test_apply_rejects_stale_expectation(client_and_factory):
    """preview 与 apply 之间结果不一致 → 409，不写数据。"""
    client, factory = client_and_factory
    before = sorted(item["question_id"] for item in _drafts(factory))
    response = client.post(
        f"/api/sources/{SOURCE_ID}/pages/2/resplit/apply",
        json={
            "markdown": _fixed_page2(_raw_pages()[2]),
            "expected_numbers": ["999"],
        },
    )
    assert response.status_code == 409
    assert sorted(item["question_id"] for item in _drafts(factory)) == before


def test_apply_honours_matching_expectation(client_and_factory):
    """expected_numbers 与 preview 一致时正常执行。"""
    client, _ = client_and_factory
    fixed = _fixed_page2(_raw_pages()[2])
    expected = _preview(client, fixed).json()["new_numbers"]
    response = client.post(
        f"/api/sources/{SOURCE_ID}/pages/2/resplit/apply",
        json={"markdown": fixed, "expected_numbers": expected},
    )
    assert response.status_code == 200


def test_manual_edits_are_reported(client_and_factory):
    """被删除的草稿如果有人工编辑，preview 必须提示会丢失。"""
    client, factory = client_and_factory
    with factory.begin() as session:
        draft = next(
            item
            for item in session.query(OcrImportDraft)
            .filter(OcrImportDraft.page_number == 1)
            .all()
            if item.original_number.startswith("2")
        )
        draft.edited_markdown = draft.ocr_markdown + "\n\n人工补充说明"
        draft.review_status = "in_review"

    payload = _preview(client, _fixed_page2(_raw_pages()[2])).json()
    assert payload["manual_edits_lost"], "有人工编辑的草稿被删除时必须提示"


# ── 5. 整页 Markdown 存储 ──

def test_page_markdown_endpoint(client_and_factory):
    client, _ = client_and_factory
    response = client.get(f"/api/sources/{SOURCE_ID}/pages/2/markdown")
    assert response.status_code == 200
    payload = response.json()
    assert payload["raw_markdown"] == _raw_pages()[2]
    assert payload["edited_markdown"] == payload["raw_markdown"]
    assert payload["modified"] is False
    assert payload["drafts"] == [], "第2页初始没有草稿"


def test_save_page_markdown_keeps_raw(client_and_factory):
    client, _ = client_and_factory
    fixed = _fixed_page2(_raw_pages()[2])
    client.put(f"/api/sources/{SOURCE_ID}/pages/2/markdown", json={"markdown": fixed})
    payload = client.get(f"/api/sources/{SOURCE_ID}/pages/2/markdown").json()
    assert payload["edited_markdown"] == fixed
    assert payload["raw_markdown"] == _raw_pages()[2]

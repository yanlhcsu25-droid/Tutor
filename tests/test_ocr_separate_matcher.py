from __future__ import annotations

import pytest

"""separate OCR 匹配与发布语义测试（对应第二十节验收场景）。

仅覆盖 solution_mode='separate'；inline 模式行为见 test_ocr_*_inline* 套件，
本文件末尾用最小用例确认 inline 未被影响。
"""

from sqlalchemy import create_engine

from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import OcrImportDraft, OcrImportSource, Question
from calculus_agent.workbench.database import WorkbenchDatabase
from calculus_agent.workbench.import_pipeline import DocumentLayout, import_document
from calculus_agent.workbench.ocr import persist_rendered_draft, render_drafts
from calculus_agent.ocr.import_service import publish_ocr_draft
from calculus_agent.workbench.resplit import apply_plan, build_plan


SOURCE_ID = "src_" + "b" * 32


def _database(tmp_path, question_page: str, solution_page: str, layout=None):
    url = f"sqlite:///{tmp_path / 'sep.db'}"
    create_schema(url)
    factory = build_session_factory(url)
    layout = layout or DocumentLayout("separate", [1], [2])
    with factory.begin() as session:
        session.add(OcrImportSource(
            id=SOURCE_ID, original_name="paper.pdf", stored_path="/tmp/paper.pdf",
            sha256="d" * 64, page_count=2, processing_status="completed",
            layout_json=layout.to_dict(),
        ))
        session.flush()
        db = WorkbenchDatabase(session)
        db.upsert_page(SOURCE_ID, 1, question_page)
        db.upsert_page(SOURCE_ID, 2, solution_page)
        for placed in import_document([(1, question_page), (2, solution_page)], layout).candidates:
            for draft in render_drafts(placed):
                persist_rendered_draft(db, source_file_id=SOURCE_ID, draft=draft)
    return factory


def _statuses(result):
    return {c.candidate.original_number: c.candidate.match_status for c in result.candidates}


def _run(question_page: str, solution_page: str):
    layout = DocumentLayout("separate", [1], [2])
    return import_document([(1, question_page), (2, solution_page)], layout)


# ── 1. 题号完全一致，三题全配对 ──

def test_all_three_match(tmp_path):
    result = _run("1. A\n2. B\n3. C", "1. a\n2. b\n3. c")
    assert _statuses(result) == {"1": "matched", "2": "matched", "3": "matched"}
    assert result.diagnostics.unmatched_solutions == []
    assert result.diagnostics.missing_questions == []


# ── 2. 1,3 + 1,2 → 1 配对，3 缺答案，答案2 多余；禁止 3→2 ──

def test_missing_and_extra_no_wrong_fallback(tmp_path):
    result = _run("1. A\n3. C", "1. a\n2. b")
    assert _statuses(result) == {"1": "matched", "3": "missing_answer"}
    extra = [s.key[1] for s in result.diagnostics.unmatched_solutions]
    assert "2" in extra
    # 关键：题3 绝不能被位置强行配到答案2
    q3 = next(c.candidate for c in result.candidates if c.candidate.original_number == "3")
    assert q3.answer == ""
    assert "未找到" in (q3.review_note or "")


# ── 3. 1,2,3 + 1,3 → 2 缺答案，答案3 不配给题2 ──

def test_missing_middle_not_stolen_by_later_answer(tmp_path):
    result = _run("1. A\n2. B\n3. C", "1. a\n3. c")
    assert _statuses(result) == {"1": "matched", "2": "missing_answer", "3": "matched"}
    q2 = next(c.candidate for c in result.candidates if c.candidate.original_number == "2")
    assert q2.answer == ""


# ── 4. 子题 3(1),3(2),3(3) + 答案 3(1),3(3) → 3(2) 缺答案 ──

def test_subquestion_missing_middle(tmp_path):
    q = "3. 求下列各值：\n(1) a\n(2) b\n(3) c"
    s = "3. (1) 答案甲\n(3) 答案丙"
    result = _run(q, s)
    assert _statuses(result) == {
        "3(1)": "matched", "3(2)": "missing_answer", "3(3)": "matched"
    }


# ── 5. 同一题号两份答案 → ambiguous，不进入待审核 ──

def test_duplicate_answer_is_ambiguous(tmp_path):
    result = _run("1. A\n2. B", "1. 答案一\n1. 答案二\n2. 答案二")
    assert _statuses(result)["1"] == "ambiguous"
    assert "-:1" in result.diagnostics.ambiguous_keys
    # 两份答案都进 unmatched，不静默挑一份
    assert [s.key[1] for s in result.diagnostics.unmatched_solutions].count("1") == 2


# ── 6/7/8. 分类正确性 ──

def test_match_status_classification(tmp_path):
    result = _run("1. A\n3. C", "1. a\n2. b")
    missing = {m.key[1] for m in result.diagnostics.missing_questions}
    extra = {s.key[1] for s in result.diagnostics.unmatched_solutions}
    assert missing == {"3"}
    assert extra == {"2"}
    # 配对成功候选可进入待审核；缺答案/多余答案不混进正常配对集合
    matched = [n for n, st in _statuses(result).items() if st == "matched"]
    assert matched == ["1"]


# ── 9. 候选未发布 → 无正式 question_id ──

def test_unpublished_has_no_formal_id(tmp_path):
    factory = _database(tmp_path, "1. A\n2. B", "1. a\n2. b")
    with factory.begin() as session:
        drafts = WorkbenchDatabase(session).list_questions(SOURCE_ID)
        assert len(drafts) == 2
        assert all(item["formal_question_id"] is None for item in drafts)


# ── 10. 发布候选 → 创建正式 question_id ──

def test_publish_creates_formal_id(tmp_path):
    factory = _database(tmp_path, "1. A\n2. B", "1. a\n2. b")
    with factory.begin() as session:
        draft = session.query(OcrImportDraft).filter_by(original_number="1").one()
        res = publish_ocr_draft(session, draft)
        assert res is not None and res["question_id"]
    with factory.begin() as session:
        draft = session.query(OcrImportDraft).filter_by(original_number="1").one()
        assert draft.formal_question_id == res["question_id"]
        assert session.get(Question, res["question_id"]) is not None


# ── 11. 正式题 revision → 重新发布仍用原 question_id ──

def test_revision_reuses_formal_id(tmp_path):
    factory = _database(tmp_path, "1. A\n2. B", "1. a\n2. b")
    with factory.begin() as session:
        draft = session.query(OcrImportDraft).filter_by(original_number="1").one()
        first = publish_ocr_draft(session, draft)["question_id"]
    with factory.begin() as session:
        db = WorkbenchDatabase(session)
        draft = session.query(OcrImportDraft).filter_by(original_number="1").one()
        assert draft.review_status == "published"
        rev = db.create_revision(draft.id)
        rev_draft = session.get(OcrImportDraft, rev["question_id"])
        second = publish_ocr_draft(session, rev_draft)["question_id"]
        assert second == first


# ── 12. 修改题号后 resplit → 按最新题号重配，不继承错误旧答案 ──

def test_resplit_after_number_change_rematches(tmp_path):
    factory = _database(tmp_path, "1. A\n2. B", "1. a\n2. b")
    changed = "1. A\n3. C"  # 题目2 改为 3
    with factory.begin() as session:
        plan = build_plan(WorkbenchDatabase(session), SOURCE_ID, 1, changed)
    # 答案2 成为无对应题目的多余答案
    assert "2" in [s.key[1] for s in plan.diagnostics.unmatched_solutions]
    q3 = next(item for item in plan.added if item.original_number == "3")
    assert "未找到" in q3.markdown
    with factory.begin() as session:
        apply_plan(WorkbenchDatabase(session), SOURCE_ID, 1, changed, expected_numbers=plan.new_numbers)
        drafts = WorkbenchDatabase(session).list_questions(SOURCE_ID)
        nums = {d["original_number"]: d["match_status"] for d in drafts}
    assert nums == {"1": "matched", "3": "missing_answer"}


# ── 13. 普通 inline OCR 行为完全不变 ──

def test_inline_untouched(tmp_path):
    layout = DocumentLayout("inline", [1], [])
    # inline：题目紧跟答案，不走 matcher，全部 matched
    result = import_document([(1, "1. A\n答案：a\n2. B\n答案：b")], layout)
    assert all(c.candidate.match_status == "matched" for c in result.candidates)
    assert result.diagnostics.unmatched_solutions == []
    assert result.diagnostics.missing_questions == []


# ── 1（补）. 缺答案/歧义候选仍出现在 OCR 待审核列表，不被过滤 ──

def test_missing_answer_still_in_review_list(tmp_path):
    # 题目 1,3 + 答案 1,2 → 1 配对成功，3 缺答案，答案2 多余
    factory = _database(tmp_path, "1. A\n3. C", "1. a\n2. b")
    with factory.begin() as session:
        drafts = WorkbenchDatabase(session).list_questions(SOURCE_ID)
        nums = {d["original_number"]: d["match_status"] for d in drafts}
    # 缺答案题仍返回，列表不过滤；仅发布被拦截
    assert nums == {"1": "matched", "3": "missing_answer"}


# ── 2（补）. 历史 separate 草稿迁移回填为 unknown，inline/legacy 保持 matched ──

def test_migration_backfills_separate_as_unknown(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    eng = create_engine(url)
    # 模拟「加列前」的最小历史 schema（无 match_status 列）
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE ocr_import_source (id VARCHAR(36) PRIMARY KEY, layout_json JSON)")
        c.exec_driver_sql(
            "CREATE TABLE ocr_import_draft ("
            "id VARCHAR(36) PRIMARY KEY, source_id VARCHAR(36),"
            "page_number INTEGER, original_number VARCHAR(64),"
            "ocr_markdown TEXT, edited_markdown TEXT, review_status VARCHAR(20))"
        )
        c.exec_driver_sql("INSERT INTO ocr_import_source (id, layout_json) VALUES ('s_sep','{\"solution_mode\":\"separate\"}')")
        c.exec_driver_sql("INSERT INTO ocr_import_source (id, layout_json) VALUES ('s_inl','{\"solution_mode\":\"inline\"}')")
        c.exec_driver_sql("INSERT INTO ocr_import_source (id) VALUES ('s_leg')")
        c.exec_driver_sql("INSERT INTO ocr_import_draft (id,source_id,page_number,original_number,ocr_markdown,edited_markdown,review_status) VALUES ('d_sep','s_sep',1,'1','m','m','pending')")
        c.exec_driver_sql("INSERT INTO ocr_import_draft (id,source_id,page_number,original_number,ocr_markdown,edited_markdown,review_status) VALUES ('d_inl','s_inl',1,'2','m','m','pending')")
        c.exec_driver_sql("INSERT INTO ocr_import_draft (id,source_id,page_number,original_number,ocr_markdown,edited_markdown,review_status) VALUES ('d_leg','s_leg',1,'3','m','m','pending')")
    create_schema(url)  # 检测到缺列 → 加列 + 回填
    with eng.connect() as c:
        rows = {r[0]: r[1] for r in c.exec_driver_sql("SELECT id, match_status FROM ocr_import_draft")}
    assert rows["d_sep"] == "unknown"   # separate 历史草稿：状态未知，须重切/重配
    assert rows["d_inl"] == "matched"   # inline 普通 OCR：默认已配对
    assert rows["d_leg"] == "matched"   # legacy（无 layout）：按 inline 处理


# ── 3（补）. match_status=unknown 的历史草稿被发布拦截 ──

@pytest.mark.parametrize("blocked_status", ["missing_answer", "ambiguous", "unknown"])
def test_unconfirmed_match_status_blocks_publish(tmp_path, monkeypatch, blocked_status):
    url = f"sqlite:///{tmp_path / 'pub.db'}"
    create_schema(url)
    factory = build_session_factory(url)
    with factory.begin() as session:
        session.add(OcrImportSource(
            id=SOURCE_ID, original_name="paper.pdf", stored_path="/tmp/paper.pdf",
            sha256="d" * 64, page_count=2, processing_status="completed",
            layout_json=DocumentLayout("separate", [1], [2]).to_dict(),
        ))
        session.flush()
        db = WorkbenchDatabase(session)
        db.upsert_page(SOURCE_ID, 1, "1. A")
        db.upsert_page(SOURCE_ID, 2, "1. a")
        for placed in import_document([(1, "1. A"), (2, "1. a")], DocumentLayout("separate", [1], [2])).candidates:
            for draft in render_drafts(placed):
                persist_rendered_draft(db, source_file_id=SOURCE_ID, draft=draft)
        draft = session.query(OcrImportDraft).filter_by(original_number="1").one()
        draft.match_status = blocked_status
        draft.review_status = "reviewed"
        session.flush()
        draft_id = draft.id

    # 让发布端点指向临时库：重绑模块级 session factory（app 在导入时已按当时 url 绑定）
    monkeypatch.setenv("CALCULUS_AGENT_DATABASE_URL", url)
    from calculus_agent.workbench import app as app_mod
    app_mod.get_settings.cache_clear()
    app_mod._session_factory = build_session_factory(app_mod.get_settings().database_url)
    from fastapi.testclient import TestClient
    client = TestClient(app_mod.app)
    resp = client.post("/api/publish", json={"question_ids": [draft_id]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["failure_count"] == 1
    assert blocked_status in body["failures"][0]["reason"]

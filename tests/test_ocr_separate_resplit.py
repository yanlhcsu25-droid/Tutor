from __future__ import annotations

from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import OcrImportDraft, OcrImportSource
from calculus_agent.workbench.database import WorkbenchDatabase
from calculus_agent.workbench.import_pipeline import DocumentLayout, import_document
from calculus_agent.workbench.ocr import persist_rendered_draft, render_drafts
from calculus_agent.workbench.resplit import ResplitBlockedError, apply_plan, build_plan


SOURCE_ID = "src_" + "a" * 32
LAYOUT = DocumentLayout("separate", [1], [2])
QUESTION_PAGE = "1. 求极限\n\n2. 求导数"
SOLUTION_PAGE = "1. 答案：1\n\n2. 答案：2x"


def _database(tmp_path, *, layout=LAYOUT):
    url = f"sqlite:///{tmp_path / 'layout.db'}"
    create_schema(url)
    factory = build_session_factory(url)
    with factory.begin() as session:
        session.add(OcrImportSource(
            id=SOURCE_ID, original_name="paper.pdf", stored_path="/tmp/paper.pdf",
            sha256="c" * 64, page_count=2, processing_status="completed",
            layout_json=layout.to_dict() if layout else None,
        ))
        session.flush()
        db = WorkbenchDatabase(session)
        db.upsert_page(SOURCE_ID, 1, QUESTION_PAGE)
        db.upsert_page(SOURCE_ID, 2, SOLUTION_PAGE)
        effective_layout = layout or DocumentLayout("inline", [1, 2], [])
        for placed in import_document([(1, QUESTION_PAGE), (2, SOLUTION_PAGE)], effective_layout).candidates:
            for draft in render_drafts(placed):
                persist_rendered_draft(db, source_file_id=SOURCE_ID, draft=draft)
    return factory


def test_layout_persists_across_new_sessions(tmp_path):
    factory = _database(tmp_path)
    with factory.begin() as session:
        stored = WorkbenchDatabase(session).get_source(SOURCE_ID)["layout"]
    assert DocumentLayout.from_dict(stored) == LAYOUT
    with factory.begin() as session:
        assert WorkbenchDatabase(session).get_source(SOURCE_ID)["layout"]["solution_mode"] == "separate"


def test_legacy_source_has_explicit_inline_fallback(tmp_path):
    factory = _database(tmp_path, layout=None)
    with factory.begin() as session:
        source = WorkbenchDatabase(session).get_source(SOURCE_ID)
    assert DocumentLayout.from_dict(source["layout"], available_pages=[1, 2]) == DocumentLayout("inline", [1, 2], [])


def test_solution_page_change_rematches_and_updates_draft(tmp_path):
    factory = _database(tmp_path)
    changed = "1. 答案：42\n\n2. 答案：2x"
    with factory.begin() as session:
        db = WorkbenchDatabase(session)
        plan = build_plan(db, SOURCE_ID, 2, changed)
        assert {item.original_number for item in plan.added} == {"1"}
        assert {item["original_number"] for item in plan.removed} == {"1"}
        assert {item["original_number"] for item in plan.kept} == {"2"}
        assert all(item.page_number == 1 for item in plan.added)
        apply_plan(db, SOURCE_ID, 2, changed, expected_numbers=plan.new_numbers)
    with factory.begin() as session:
        drafts = WorkbenchDatabase(session).list_questions(SOURCE_ID)
        q1 = next(item for item in drafts if item["original_number"] == "1")
        assert "答案：42" in q1["ocr_markdown"]


def test_question_number_change_reruns_matcher_and_keeps_diagnostics(tmp_path):
    factory = _database(tmp_path)
    changed = "1. 求极限\n\n3. 求导数"
    with factory.begin() as session:
        plan = build_plan(WorkbenchDatabase(session), SOURCE_ID, 1, changed)
    assert "2" in [item.key[1] for item in plan.diagnostics.unmatched_solutions]
    q3 = next(item for item in plan.added if item.original_number == "3")
    assert "未找到" in q3.markdown


def test_ambiguous_solution_is_not_overwritten_during_resplit(tmp_path):
    factory = _database(tmp_path)
    changed = "1. 答案：甲\n\n1. 答案：乙\n\n2. 答案：2x"
    with factory.begin() as session:
        plan = build_plan(WorkbenchDatabase(session), SOURCE_ID, 2, changed)
    assert "-:1" in plan.diagnostics.ambiguous_keys
    assert [item.key[1] for item in plan.diagnostics.unmatched_solutions].count("1") == 2
    q1 = next(item for item in plan.added if item.original_number == "1")
    assert "题号重复" in q1.markdown
    assert "答案：甲" not in q1.markdown and "答案：乙" not in q1.markdown


def test_changed_published_separate_draft_blocks_apply(tmp_path):
    factory = _database(tmp_path)
    changed = "1. 答案：99\n\n2. 答案：2x"
    with factory.begin() as session:
        db = WorkbenchDatabase(session)
        draft = next(item for item in db._session.query(OcrImportDraft).all() if item.original_number == "1")
        draft.review_status = "published"
    with factory.begin() as session:
        plan = build_plan(WorkbenchDatabase(session), SOURCE_ID, 2, changed)
        assert [item["original_number"] for item in plan.blocking] == ["1"]
        try:
            apply_plan(WorkbenchDatabase(session), SOURCE_ID, 2, changed)
        except ResplitBlockedError:
            pass
        else:
            raise AssertionError("published 套卷草稿发生变化时必须阻止 apply")


def test_unchanged_full_reimport_renders_identically(tmp_path):
    factory = _database(tmp_path)
    with factory.begin() as session:
        db = WorkbenchDatabase(session)
        plan = build_plan(db, SOURCE_ID, 2, SOLUTION_PAGE)
        assert plan.added == [] and plan.removed == []
        assert len(plan.kept) == 2

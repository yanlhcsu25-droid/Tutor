from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.models import OcrImportSource, Question, QuestionDraft
from calculus_agent.workbench.database import WorkbenchDatabase


def _source_id(char: str) -> str:
    return "src_" + char * 32


@pytest.fixture
def delete_env(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'delete.db'}"
    create_schema(db_url)
    factory = build_session_factory(db_url)
    import calculus_agent.workbench.app as app_mod

    files = tmp_path / "files"
    raw = tmp_path / "ocr_raw"
    cache = tmp_path / "page_cache"
    for path in (files, raw, cache):
        path.mkdir()
    monkeypatch.setattr(app_mod, "_session_factory", factory)
    monkeypatch.setattr(app_mod, "FILES_ROOT", files)
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(app_mod, "PAGE_CACHE_ROOT", cache)
    app_mod._ocr_cancel_events.clear()
    app_mod._ocr_delete_requests.clear()
    return TestClient(app_mod.app), factory, files, raw, cache


def _create_source(factory, files: Path, char: str, *, status: str = "pending") -> str:
    source_id = _source_id(char)
    pdf = files / f"{source_id}.pdf"
    pdf.write_bytes(f"pdf-{char}".encode())
    with factory.begin() as session:
        session.add(OcrImportSource(
            id=source_id, original_name=f"{char}.pdf", stored_path=str(pdf),
            sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(), page_count=1,
            processing_status="completed", layout_json=None,
        ))
        session.flush()
        db = WorkbenchDatabase(session)
        db.upsert_page(source_id, 1, "1. 题目")
        db.add_question(
            question_id="q_" + char * 32, source_file_id=source_id,
            page_number=1, original_number="1", bbox=None,
            ocr_markdown="## 题目内容\n\n题目\n\n## 题型\n\ncalculation\n",
        )
        draft = db.get_question("q_" + char * 32)
        if status == "in_review":
            db.save_question(draft["question_id"], draft["edited_markdown"] + "\n人工修改")
        elif status == "reviewed":
            db.mark_reviewed(draft["question_id"])
    return source_id


@pytest.mark.parametrize(("status", "char"), [("pending", "a"), ("in_review", "b"), ("reviewed", "c")])
def test_unpublished_review_states_can_be_deleted(delete_env, status, char):
    client, factory, files, _, _ = delete_env
    source_id = _create_source(factory, files, char, status=status)
    response = client.delete(f"/api/sources/{source_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    with factory.begin() as session:
        db = WorkbenchDatabase(session)
        with pytest.raises(KeyError):
            db.get_source(source_id)
        assert db.list_questions(source_id) == []
        assert db.list_pages(source_id) == []


def test_summary_aggregates_pending_progress_and_completed(delete_env):
    _, factory, files, _, _ = delete_env
    pending = _create_source(factory, files, "a")
    progress = _create_source(factory, files, "b")
    completed = _create_source(factory, files, "c", status="reviewed")
    with factory.begin() as session:
        db = WorkbenchDatabase(session)
        # Add one completed draft beside one pending draft.
        db.add_question(
            question_id="q_" + "d" * 32, source_file_id=progress, page_number=1,
            original_number="2", bbox=None, ocr_markdown="## 题目内容\n\n第二题",
        )
        db.mark_reviewed("q_" + "b" * 32)
        summaries = {item["source_file_id"]: item for item in db.list_sources()}
    assert summaries[pending]["review"] == {"status": "pending", "completed": 0, "total": 1}
    assert summaries[progress]["review"] == {"status": "in_progress", "completed": 1, "total": 2}
    assert summaries[completed]["review"] == {"status": "completed", "completed": 1, "total": 1}


def test_question_list_includes_source_for_zero_question_recovery(delete_env):
    client, factory, files, _, _ = delete_env
    source_id = _source_id("a")
    pdf = files / f"{source_id}.pdf"
    pdf.write_bytes(b"pdf-z")
    with factory.begin() as session:
        session.add(OcrImportSource(
            id=source_id,
            original_name="zero.pdf",
            stored_path=str(pdf),
            sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),
            page_count=2,
            processing_status="failed",
            processing_error="题目切分结果为0",
            layout_json={"progress": {"current_page": 2, "total_pages": 2}},
        ))

    response = client.get(f"/api/sources/{source_id}/questions")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["source"]["source_file_id"] == source_id
    assert response.json()["source"]["page_count"] == 2


def test_delete_active_upload_requests_stop_before_cleanup(delete_env):
    client, factory, files, _, _ = delete_env
    source_id = _create_source(factory, files, "d")
    import calculus_agent.workbench.app as app_mod

    event = threading.Event()
    app_mod._ocr_cancel_events[source_id] = event
    response = client.delete(f"/api/sources/{source_id}")

    assert response.status_code == 200
    assert response.json() == {
        "source_file_id": source_id,
        "deleted": False,
        "status": "deleting",
    }
    assert event.is_set()
    assert source_id in app_mod._ocr_delete_requests
    with factory.begin() as session:
        source = WorkbenchDatabase(session).get_source(source_id)
    assert source["processing_status"] == "deleting"
    assert files.joinpath(f"{source_id}.pdf").exists()


def test_delete_reports_manual_edits_and_cleans_source_files_only(delete_env):
    client, factory, files, raw, cache = delete_env
    target = _create_source(factory, files, "e", status="in_review")
    other = _create_source(factory, files, "f")
    with factory.begin() as session:
        WorkbenchDatabase(session).save_page_markdown(target, 1, "1. 人工修改题目")
    (raw / target).mkdir()
    (raw / target / "page.md").write_text("ocr")
    (cache / target).mkdir()
    (cache / target / "page.png").write_bytes(b"png")
    response = client.delete(f"/api/sources/{target}")
    body = response.json()
    assert body["had_manual_edits"] is True
    assert body["deleted_page_count"] == 1 and body["deleted_draft_count"] == 1
    assert not (files / f"{target}.pdf").exists()
    assert not (raw / target).exists() and not (cache / target).exists()
    assert (files / f"{other}.pdf").exists()
    with factory.begin() as session:
        assert WorkbenchDatabase(session).get_source(other)["source_file_id"] == other


def _publish_one(factory, source_id: str) -> None:
    with factory.begin() as session:
        qdraft = QuestionDraft(
            source_name="ocr_import", source_item_id="q_" + source_id[4] * 32,
            variant=1, subject="高等数学", question_type="calculation",
            question_text="题目", normalized_fingerprint=source_id[4] * 64,
            status="approved", proposed_classification_json={},
        )
        session.add(qdraft)
        session.flush()
        session.add(Question(
            draft_id=qdraft.id, question_text="题目", question_type="calculation",
            verification_status="manual_verified", review_status="approved",
        ))


def test_any_formal_question_blocks_entire_delete(delete_env):
    client, factory, files, _, _ = delete_env
    source_id = _create_source(factory, files, "a")
    with factory.begin() as session:
        WorkbenchDatabase(session).add_question(
            question_id="q_" + "b" * 32, source_file_id=source_id,
            page_number=1, original_number="2", bbox=None,
            ocr_markdown="## 题目内容\n\n未发布的第二题",
        )
    _publish_one(factory, source_id)
    response = client.delete(f"/api/sources/{source_id}")
    assert response.status_code == 409
    with factory.begin() as session:
        db = WorkbenchDatabase(session)
        assert db.get_source(source_id)["can_delete"] is False
        assert len(db.list_questions(source_id)) == 2
        assert len(db.list_pages(source_id)) == 1
    assert (files / f"{source_id}.pdf").exists()


def test_approved_bank_draft_without_formal_question_is_deleted(delete_env):
    client, factory, files, _, _ = delete_env
    source_id = _create_source(factory, files, "a")
    with factory.begin() as session:
        session.add(QuestionDraft(
            source_name="ocr_import", source_item_id="q_" + "a" * 32,
            variant=1, subject="高等数学", question_type="calculation",
            question_text="题目", normalized_fingerprint="a" * 64,
            status="approved", proposed_classification_json={},
        ))
    response = client.delete(f"/api/sources/{source_id}")
    assert response.status_code == 200
    assert response.json()["deleted_bank_draft_count"] == 1
    with factory.begin() as session:
        assert session.query(QuestionDraft).count() == 0


def test_missing_files_do_not_block_delete_and_sha_can_be_reused(delete_env):
    client, factory, files, _, _ = delete_env
    source_id = _create_source(factory, files, "a")
    with factory.begin() as session:
        old = WorkbenchDatabase(session).get_source(source_id)
    (files / f"{source_id}.pdf").unlink()
    response = client.delete(f"/api/sources/{source_id}")
    assert response.status_code == 200
    with factory.begin() as session:
        recreated = WorkbenchDatabase(session).create_source(
            _source_id("b"), "same.pdf", files / f"{_source_id('b')}.pdf", old["sha256"]
        )
        assert recreated["source_file_id"] == _source_id("b")


def test_same_pdf_can_be_imported_with_distinct_ocr_fingerprints(delete_env):
    _, factory, files, _, _ = delete_env
    content_sha = "a" * 64
    with factory.begin() as session:
        db = WorkbenchDatabase(session)
        first = db.create_source(
            _source_id("a"), "same.pdf", files / "a.pdf", content_sha,
            import_fingerprint="1" * 64,
        )
        second = db.create_source(
            _source_id("b"), "same.pdf", files / "b.pdf", content_sha,
            import_fingerprint="2" * 64,
        )
        duplicate = db.create_source(
            _source_id("c"), "same.pdf", files / "c.pdf", content_sha,
            import_fingerprint="1" * 64,
        )
    assert first["source_file_id"] == _source_id("a")
    assert second["source_file_id"] == _source_id("b")
    assert duplicate["source_file_id"] == _source_id("a")
    assert first["sha256"] == content_sha

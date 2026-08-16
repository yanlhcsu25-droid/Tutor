"""回归测试：原始题号校验兼容性 + 结构化元数据不被 Markdown 污染。

涵盖验收发现的四类数据污染问题：
1. original_number validator 兼容 splitter 产生的合法层级题号
2. knowledge_points 以结构化字段保存，绝不把 UUID 写进 Markdown
3. 保存元数据不产生 literal \\n，也不重复追加 ## 难度
4. 保存一次/连续修改难度具备幂等性
"""

import pytest

from calculus_agent.workbench.markdown_schema import fixed_template, payload_from_markdown
from calculus_agent.workbench.models import QuestionPayload
from calculus_agent.models import OcrImportDraft, OcrImportSource
from fastapi.testclient import TestClient


QUESTION_ID = "q_" + "a" * 32
SOURCE_ID = "src_" + "b" * 32

# 项目历史中已有的合法原始题号格式（splitter / resplit 真实产出）
LEGAL_ORIGINAL_NUMBERS = [
    "3",
    "3(1)",
    "3(2)",
    "4(5)",
    "一",
    "一(1)",
    "3.1",
    "2-1",
    "3-1-2",
]


@pytest.fixture
def env(tmp_path):
    import calculus_agent.workbench.app as app_mod
    from calculus_agent.db import build_session_factory, create_schema

    db_url = f"sqlite:///{tmp_path / 'meta.db'}"
    create_schema(db_url)
    factory = build_session_factory(db_url)
    files = tmp_path / "files"
    files.mkdir()
    pdf = files / f"{SOURCE_ID}.pdf"
    pdf.write_bytes(b"pdf")
    monkeypatch = None
    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(app_mod, "_session_factory", factory)
    monkeypatch.setattr(app_mod, "FILES_ROOT", files)
    monkeypatch.setattr(app_mod, "DATA_ROOT", tmp_path)
    yield TestClient(app_mod.app), factory, files
    monkeypatch.undo()


def _seed(
    factory,
    *,
    original_number="3(1)",
    knowledge_points=None,
    difficulty=None,
    answer="测试答案",
):
    with factory.begin() as session:
        session.add(OcrImportSource(
            id=SOURCE_ID, original_name="x.pdf", stored_path="/tmp/x.pdf",
            sha256="x", page_count=1, processing_status="completed", layout_json=None,
        ))
        md = fixed_template(
            "题目正文", question_type="calculation", page_number=1,
            original_number=original_number,
            answer=answer,
        )
        draft = OcrImportDraft(
            id=QUESTION_ID, source_id=SOURCE_ID, page_number=1,
            original_number=original_number, ocr_markdown=md, edited_markdown=md,
            review_status="in_review", knowledge_points_json=knowledge_points or [],
            difficulty_level=difficulty,
        )
        session.add(draft)
        session.flush()


# ── 1. original_number validator 兼容性 ──

@pytest.mark.parametrize("number", LEGAL_ORIGINAL_NUMBERS)
def test_original_number_validator_accepts_legal_formats(number):
    md = fixed_template(
        "题目", question_type="calculation", page_number=1, original_number=number,
    )
    payload, validation = payload_from_markdown(
        md, question_id=QUESTION_ID, source_file_id=SOURCE_ID,
        ocr_markdown=md, source_bbox=None,
    )
    assert validation.valid, f"{number} 被错误拒绝: {validation.issues}"
    assert payload is not None
    assert payload.original_number == number


def test_original_number_validator_still_rejects_garbage():
    with pytest.raises(Exception):
        QuestionPayload.model_validate({
            "question_id": QUESTION_ID,
            "source_file_id": SOURCE_ID,
            "page_number": 1,
            "original_number": "3(a)",  # 子题标识含字母，非法
            "question_type": "calculation",
            "question_content": "x",
            "ocr_markdown": "m",
            "edited_markdown": "m",
        })


# ── 2. knowledge UUID 不得进入 Markdown ──

def test_save_metadata_stores_knowledge_ids(env):
    client, factory, _ = env
    _seed(factory, knowledge_points=[], difficulty=None)
    uuid_a = "7439051e-a023-4281-adad-f97150c9f796"
    uuid_b = "d3e5d237-9e8a-4a13-a7b2-25a82c22da03"
    resp = client.put(
        f"/api/questions/{QUESTION_ID}/metadata",
        json={"knowledge_points": [uuid_a, uuid_b], "difficulty_level": 2,
              "content_confirmed": True},
    )
    assert resp.status_code == 200, resp.text
    q = resp.json()["question"]
    # 关键：knowledge_points_json 保存的是 knowledge_id（UUID），不是名称
    assert q["knowledge_points"] == [uuid_a, uuid_b]
    assert q["difficulty_level"] == 2
    # 关键：UUID 只存在于结构化字段，绝不进入 Markdown
    assert uuid_a not in q["edited_markdown"]
    assert uuid_b not in q["edited_markdown"]


def test_save_metadata_rejects_more_than_three(env):
    client, factory, _ = env
    _seed(factory)
    resp = client.put(
        f"/api/questions/{QUESTION_ID}/metadata",
        json={"knowledge_points": [
            "7439051e-a023-4281-adad-f97150c9f796",
            "d3e5d237-9e8a-4a13-a7b2-25a82c22da03",
            "60165f8f-d76a-46ce-ae17-81da39f2b817",
            "11111111-1111-1111-1111-111111111111",
        ], "difficulty_level": 3},
    )
    assert resp.status_code == 200
    assert len(resp.json()["question"]["knowledge_points"]) == 3


# ── 3. 不写 literal \n，不重复追加 ## 难度 ──

def test_save_metadata_is_idempotent_and_does_not_corrupt_markdown(env):
    client, factory, _ = env
    _seed(factory, original_number="3(1)")
    # 连续修改难度 2 -> 3 -> 4，必须幂等且不产生任何 ## 难度 section
    # （难度已改为结构化字段 difficulty_level，不再写入 Markdown）
    for level in (2, 3, 4):
        resp = client.put(
            f"/api/questions/{QUESTION_ID}/metadata",
            json={"knowledge_points": ["7439051e-a023-4281-adad-f97150c9f796"], "difficulty_level": level},
        )
        assert resp.status_code == 200
        md = resp.json()["question"]["edited_markdown"]
        assert r"\n" not in md, f"出现 literal \\n: {md!r}"
        assert md.count("## 难度") == 0, f"不应出现 ## 难度 section: {md!r}"
        assert md.count("## 知识点") == 0, f"不应出现 ## 知识点 section: {md!r}"
        assert md.count("## 章节") == 0, f"不应出现 ## 章节 section: {md!r}"
    # 最终难度 = 4
    final = client.get(f"/api/questions/{QUESTION_ID}").json()["question"]
    assert final["difficulty_level"] == 4


def test_save_markdown_then_metadata_keeps_metadata_source_of_truth(env):
    client, factory, _ = env
    _seed(factory, knowledge_points=["7439051e-a023-4281-adad-f97150c9f796"], difficulty=3)
    # 仅保存正文（不含知识点/难度），元数据不得被清空
    resp = client.patch(
        f"/api/questions/{QUESTION_ID}",
        json={"markdown": "## 题目内容\n\n新正文\n\n## 题型\n\ncalculation\n"},
    )
    assert resp.status_code == 200
    q = resp.json()["question"]
    assert q["knowledge_points"] == ["7439051e-a023-4281-adad-f97150c9f796"]
    assert q["difficulty_level"] == 3


def test_submit_uses_db_metadata_not_markdown(env):
    client, factory, _ = env
    uid_a = "7439051e-a023-4281-adad-f97150c9f796"
    uid_b = "d3e5d237-9e8a-4a13-a7b2-25a82c22da03"
    _seed(factory, knowledge_points=[uid_a, uid_b], difficulty=2)
    # 先确认题目内容（提交前置条件），并再次以结构化字段保存知识点/难度
    client.put(
        f"/api/questions/{QUESTION_ID}/metadata",
        json={"knowledge_points": [uid_a, uid_b], "difficulty_level": 2,
              "content_confirmed": True},
    )
    resp = client.post(f"/api/sources/{SOURCE_ID}/submit", json={"question_ids": [QUESTION_ID]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success_count"] == 1, body
    assert body["failure_count"] == 0


def test_submit_rejects_empty_solution_with_explicit_field_reason(env):
    client, factory, _ = env
    uid = "7439051e-a023-4281-adad-f97150c9f796"
    _seed(factory, knowledge_points=[uid], difficulty=2, answer="")
    client.put(
        f"/api/questions/{QUESTION_ID}/metadata",
        json={
            "knowledge_points": [uid],
            "difficulty_level": 2,
            "content_confirmed": True,
        },
    )

    response = client.post(
        f"/api/sources/{SOURCE_ID}/submit",
        json={"question_ids": [QUESTION_ID]},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["success_count"] == 0
    assert result["failure_count"] == 1
    assert "参考解答" in result["failures"][0]["reasons"][0]


# ── 5. 加载时按 knowledge_id 反显名称（不触发 AI 分类）──

def test_knowledge_options_endpoint_returns_id_name_list(env):
    client, factory, _ = env
    _seed(factory)
    resp = client.get(f"/api/questions/{QUESTION_ID}/knowledge/options")
    assert resp.status_code == 200
    body = resp.json()
    assert body["question_id"] == QUESTION_ID
    assert isinstance(body["options"], list)
    for opt in body["options"]:
        assert "knowledge_id" in opt and "name" in opt

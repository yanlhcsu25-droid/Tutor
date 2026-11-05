"""Build and validate the real-question, isolated TQ-PQ-003 fixture."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from uuid import UUID

FIXTURE_ID = "question_bank/first_three_chapters_open_v1"
# The first 16 are the in-scope pool. Six Chapter 4 items make exclusion real.
IN_SCOPE_IDS = (
    # Chapter 1: two choices, one fill/calculation, plus three proof distractors.
    "9c378f55-fb37-4718-b8fc-bff07fcc5676", "c5b8bcf6-079f-4eba-98e8-3bc1c270e14a",
    "aa3255ab-4760-44cc-a5a1-232dbef7125d", "2fdba987-9749-49ff-902e-da21e3058091",
    "1291a207-7055-478a-90b4-720ff7dc9a84",
    "9102cb70-40b2-4d95-939c-380fea2f7d8d", "1ab685d0-da44-49ea-a19f-f5e73161fc6c",
    # Chapter 2: one foundational choice and one implicit-differentiation calculation.
    "b3d77fa7-4c91-419e-9d0f-4ba4f051ce5f", "43302d57-1dd3-4de3-a866-7da1117397aa",
    # Chapter 3: one choice/fill, two diverse calculations, plus three proof distractors.
    "7838b2b1-06bc-4b62-9dd8-e0e72804c3b9", "3abc1778-90bd-4c32-9678-f2cf0113ca25",
    "a1ff67c2-d909-4407-b8f6-f6c4007e9fb9", "b59244fe-e93a-475c-9c86-8a4d8602d2a9",
    "1c715445-7eb0-43fc-9860-c0f414f14c21",
    "0914761c-8b7a-409a-b2a6-72872d1fd389", "6194e288-d34b-415a-9820-891aae486d19",
)
CHAPTER4_IDS = (
    "16110015-beea-4fcb-bffd-f363f6cc9d2f", "bf244942-2c8d-42e0-9e86-7e9e185138be",
    "fe06ab39-a41a-4dcd-98ed-ad0d9f8125db", "7da852f6-184e-4fda-a8e9-3b84a5ee9c8d",
    "da89dde6-ebe0-4ef1-9851-256be863b320", "50267c64-d09c-4c22-bc1a-1cea9c2c3192",
)
QUESTION_IDS = (*IN_SCOPE_IDS, *CHAPTER4_IDS)
DEFAULT_TYPES = {"选择题": 4, "填空题": 2, "计算题": 4}
EXCLUDED_SOURCES = {"CMM-Math", "built-in-demo", "test_source"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def build_pq003_fixture(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
        with sqlite3.connect(destination) as target:
            original.backup(target)
            marks = ",".join("?" for _ in QUESTION_IDS)
            target.execute(f"UPDATE question SET is_active=0 WHERE id NOT IN ({marks})", QUESTION_IDS)
            target.commit()
    return validate_pq003_fixture(destination, source=source)


def validate_pq003_fixture(database: Path, *, source: Path | None = None) -> dict:
    marks = ",".join("?" for _ in QUESTION_IDS)
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        user_version = db.execute("PRAGMA user_version").fetchone()[0]
        schema = "\n".join(row[0] or "" for row in db.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
        ))
        rows = db.execute(f"""
            WITH latest AS (
              SELECT question_id,MAX(profile_version) profile_version FROM question_profile
              WHERE profile_status='approved' GROUP BY question_id
            )
            SELECT q.id question_id,q.draft_id,d.source_name,d.source_item_id,c.textbook_id,
                   q.curriculum_chapter_id chapter_id,c.code chapter_code,c.title chapter_title,
                   q.question_type,q.difficulty raw_question_difficulty,p.difficulty,
                   p.profile_version,p.profile_source,p.profile_status,p.estimated_time_min,
                   q.default_score,q.verification_status,q.review_status,q.is_active,
                   q.knowledge_match_status,q.publish_source,q.question_text
            FROM question q JOIN question_draft d ON d.id=q.draft_id
            JOIN curriculum_node c ON c.id=q.curriculum_chapter_id
            LEFT JOIN latest l ON l.question_id=q.id
            LEFT JOIN question_profile p ON p.question_id=l.question_id AND p.profile_version=l.profile_version
            WHERE q.id IN ({marks}) ORDER BY c.sort_order,q.question_type,q.id
        """, QUESTION_IDS).fetchall()
        links = db.execute(f"""
            SELECT l.question_id,k.id,k.name,k.curriculum_node_id,cn.textbook_id
            FROM question_knowledge_link l JOIN knowledge_node k ON k.id=l.knowledge_node_id
            JOIN curriculum_node cn ON cn.id=k.curriculum_node_id
            WHERE l.question_id IN ({marks}) ORDER BY l.question_id,k.id
        """, QUESTION_IDS).fetchall()
        raw_link_count = db.execute(
            f"SELECT COUNT(*) FROM question_knowledge_link WHERE question_id IN ({marks})", QUESTION_IDS
        ).fetchone()[0]
        active_ids = {row[0] for row in db.execute("""
            SELECT q.id FROM question q JOIN question_draft d ON d.id=q.draft_id
            WHERE q.is_active=1 AND q.review_status='approved' AND q.knowledge_match_status='current'
              AND d.source_name NOT IN ('CMM-Math','built-in-demo','test_source')
        """)}

    knowledge: dict[str, list[dict]] = {question_id: [] for question_id in QUESTION_IDS}
    for link in links:
        knowledge[link[0]].append({
            "id": link[1], "name": link[2], "curriculum_node_id": link[3], "textbook_id": link[4],
        })
    questions = []
    for row in rows:
        item = dict(row)
        item["question_text_sha256"] = hashlib.sha256(item.pop("question_text").encode()).hexdigest()
        item["knowledge_points"] = knowledge[item["question_id"]]
        questions.append(item)

    in_scope = [item for item in questions if item["question_id"] in IN_SCOPE_IDS]
    chapter_supply = {
        code: {
            "total": sum(item["chapter_code"] == code for item in in_scope),
            "question_type_counts": dict(Counter(
                item["question_type"] for item in in_scope if item["chapter_code"] == code
            )),
            "difficulty_counts": dict(Counter(
                str(item["difficulty"]) for item in in_scope if item["chapter_code"] == code
            )),
        }
        for code in ("一", "二", "三")
    }
    default_supply = Counter(
        item["question_type"] for item in in_scope if item["question_type"] in DEFAULT_TYPES
    )
    checks = {
        "all_questions_present": len(rows) == len(QUESTION_IDS) and {r["question_id"] for r in rows} == set(QUESTION_IDS),
        "question_ids_are_uuid": all(str(UUID(question_id)) == question_id for question_id in QUESTION_IDS),
        "single_textbook": len({r["textbook_id"] for r in rows}) == 1,
        "first_three_chapters_present": {r["chapter_code"] for r in in_scope} == {"一", "二", "三"},
        "chapter_four_control_present": any(r["chapter_code"] == "四" for r in questions),
        "in_scope_pool_exceeds_default_paper": len(in_scope) > sum(DEFAULT_TYPES.values()),
        "default_blueprint_supply_exact": default_supply == Counter(DEFAULT_TYPES),
        "approved_current_active": all(r["review_status"] == "approved" and r["knowledge_match_status"] == "current" and r["is_active"] == 1 for r in rows),
        "eligible_real_sources": all(r["source_name"] not in EXCLUDED_SOURCES for r in rows),
        "approved_difficulty_available": all(r["profile_status"] == "approved" and r["difficulty"] in {1,2,3,4,5} for r in rows),
        "knowledge_links_valid": len(links) == raw_link_count and all(knowledge[q] for q in QUESTION_IDS) and all(
            link["textbook_id"] == item["textbook_id"] for item in questions for link in item["knowledge_points"]
        ),
        "candidate_pool_is_exact": active_ids == set(QUESTION_IDS),
        "question_texts_are_distinct": len({r["question_text_sha256"] for r in questions}) == len(questions),
    }
    source_supply = {}
    if source:
        with sqlite3.connect(source) as source_db:
            for code, question_type, difficulty, count in source_db.execute("""
                WITH latest AS (
                  SELECT question_id,MAX(profile_version) profile_version FROM question_profile
                  WHERE profile_status='approved' GROUP BY question_id
                )
                SELECT c.code,q.question_type,p.difficulty,COUNT(*)
                FROM question q JOIN question_draft d ON d.id=q.draft_id
                JOIN curriculum_node c ON c.id=q.curriculum_chapter_id
                JOIN latest l ON l.question_id=q.id
                JOIN question_profile p ON p.question_id=l.question_id AND p.profile_version=l.profile_version
                WHERE q.is_active=1 AND q.review_status='approved' AND q.knowledge_match_status='current'
                  AND d.source_name NOT IN ('CMM-Math','built-in-demo','test_source')
                  AND c.code IN ('一','二','三')
                GROUP BY c.code,q.question_type,p.difficulty ORDER BY c.code,q.question_type,p.difficulty
            """):
                chapter = source_supply.setdefault(code, {"total": 0, "by_type_and_difficulty": {}})
                chapter["total"] += count
                chapter["by_type_and_difficulty"][f"{question_type}/difficulty_{difficulty}"] = count

    manifest = {
        "fixture_id": FIXTURE_ID, "case_id": "TQ-PQ-003",
        "database": {
            "sqlite_user_version": user_version,
            "schema_sha256": hashlib.sha256(schema.encode()).hexdigest(),
            "source_path": str(source) if source else None,
            "source_sha256": _sha(source) if source else None,
            "fixture_sha256": _sha(database),
        },
        "question_ids": list(QUESTION_IDS), "in_scope_question_ids": list(IN_SCOPE_IDS),
        "chapter4_control_question_ids": list(CHAPTER4_IDS),
        "source_chapter_supply": source_supply, "fixture_chapter_supply": chapter_supply,
        "default_blueprint": {"total_questions": 10, "question_type_counts": DEFAULT_TYPES},
        "questions": questions, "constraint_checks": checks, "validation_passed": all(checks.values()),
    }
    manifest["manifest_sha256"] = _manifest_hash(manifest)
    if not manifest["validation_passed"]:
        raise ValueError(f"TQ-PQ-003 fixture validation failed: {[k for k,v in checks.items() if not v]}")
    return manifest

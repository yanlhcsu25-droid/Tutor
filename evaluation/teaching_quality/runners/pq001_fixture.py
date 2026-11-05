"""Build and validate the real-question, isolated TQ-PQ-001 fixture."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import UUID

FIXTURE_ID = "question_bank/basic_v1"
QUESTION_IDS = (
    "57c35e8c-eba6-4e4d-a7ac-edf8aafe759a",
    "9c378f55-fb37-4718-b8fc-bff07fcc5676",
    "aa3255ab-4760-44cc-a5a1-232dbef7125d",
    "136a45b7-c4e6-4611-a838-fdaa14fe3d31",
    "2fdba987-9749-49ff-902e-da21e3058091",
    "72c15f32-66ad-4749-880c-cc4c5a135b75",
)
EXPECTED_TYPES = {"选择题": 2, "填空题": 2, "计算题": 2}
EXCLUDED_SOURCES = {"CMM-Math", "built-in-demo", "test_source"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_pq001_fixture(source: Path, destination: Path) -> dict:
    """Copy the real DB, expose only the reviewed six-item candidate pool, validate it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
        with sqlite3.connect(destination) as target:
            original.backup(target)
            placeholders = ",".join("?" for _ in QUESTION_IDS)
            target.execute(
                f"UPDATE question SET is_active = 0 WHERE id NOT IN ({placeholders})",
                QUESTION_IDS,
            )
            target.commit()
    return validate_pq001_fixture(destination, source=source)


def validate_pq001_fixture(database: Path, *, source: Path | None = None) -> dict:
    placeholders = ",".join("?" for _ in QUESTION_IDS)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        schema = "\n".join(
            row[0] or "" for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
            )
        )
        rows = connection.execute(
            f"""
            WITH latest AS (
              SELECT question_id, MAX(profile_version) AS profile_version
              FROM question_profile WHERE profile_status = 'approved'
              GROUP BY question_id
            )
            SELECT q.id AS question_id, q.draft_id, d.source_name, d.source_item_id,
                   c.textbook_id, q.curriculum_chapter_id AS chapter_id,
                   c.code AS chapter_code, c.title AS chapter_title,
                   q.question_type, q.difficulty AS raw_question_difficulty,
                   p.difficulty, p.profile_version, p.profile_source, p.profile_status,
                   p.estimated_time_min, q.default_score, q.verification_status,
                   q.review_status, q.is_active, q.knowledge_match_status,
                   q.publish_source, q.question_text
            FROM question q
            JOIN question_draft d ON d.id = q.draft_id
            JOIN curriculum_node c ON c.id = q.curriculum_chapter_id
            LEFT JOIN latest l ON l.question_id = q.id
            LEFT JOIN question_profile p ON p.question_id = l.question_id
                                         AND p.profile_version = l.profile_version
            WHERE q.id IN ({placeholders}) ORDER BY q.id
            """,
            QUESTION_IDS,
        ).fetchall()
        links = connection.execute(
            f"""
            SELECT l.question_id, k.id, k.name, k.curriculum_node_id, cn.textbook_id
            FROM question_knowledge_link l
            JOIN knowledge_node k ON k.id = l.knowledge_node_id
            JOIN curriculum_node cn ON cn.id = k.curriculum_node_id
            WHERE l.question_id IN ({placeholders})
            ORDER BY l.question_id, k.id
            """,
            QUESTION_IDS,
        ).fetchall()
        raw_link_count = connection.execute(
            f"SELECT COUNT(*) FROM question_knowledge_link WHERE question_id IN ({placeholders})",
            QUESTION_IDS,
        ).fetchone()[0]
        active_candidate_ids = {
            row[0] for row in connection.execute(
                """
                SELECT q.id FROM question q JOIN question_draft d ON d.id=q.draft_id
                WHERE q.is_active=1 AND q.review_status='approved'
                  AND q.knowledge_match_status='current'
                  AND d.source_name NOT IN ('CMM-Math','built-in-demo','test_source')
                """
            )
        }

    links_by_question: dict[str, list[dict]] = {question_id: [] for question_id in QUESTION_IDS}
    for link in links:
        links_by_question[link[0]].append({
            "id": link[1], "name": link[2], "curriculum_node_id": link[3],
            "textbook_id": link[4],
        })

    questions = []
    for row in rows:
        item = dict(row)
        item["question_text_sha256"] = hashlib.sha256(
            item.pop("question_text").encode("utf-8")
        ).hexdigest()
        item["knowledge_points"] = links_by_question[item["question_id"]]
        questions.append(item)

    type_counts: dict[str, int] = {}
    for item in questions:
        type_counts[item["question_type"]] = type_counts.get(item["question_type"], 0) + 1

    expected_ids = set(QUESTION_IDS)
    checks = {
        "all_six_questions_present": len(rows) == 6 and {row["question_id"] for row in rows} == expected_ids,
        "question_ids_are_uuid": all(str(UUID(question_id)) == question_id for question_id in QUESTION_IDS),
        "single_textbook": len({row["textbook_id"] for row in rows}) == 1,
        "first_chapter_only": bool(rows) and all(row["chapter_code"] == "一" and row["chapter_title"] == "函数与极限" for row in rows),
        "question_types_support_default_six_item_blueprint": type_counts == EXPECTED_TYPES,
        "approved_current_active": all(row["review_status"] == "approved" and row["knowledge_match_status"] == "current" and row["is_active"] == 1 for row in rows),
        "eligible_real_sources": all(row["source_name"] not in EXCLUDED_SOURCES for row in rows),
        "approved_difficulty_available": all(row["profile_status"] == "approved" and row["difficulty"] in {2, 3, 4} for row in rows),
        "knowledge_links_valid": (
            len(links) == raw_link_count
            and all(links_by_question[question_id] for question_id in QUESTION_IDS)
            and all(
                link["textbook_id"] == item["textbook_id"]
                for item in questions for link in item["knowledge_points"]
            )
        ),
        "candidate_pool_is_exact": active_candidate_ids == expected_ids,
        "question_texts_are_distinct": len({item["question_text_sha256"] for item in questions}) == 6,
        "score_plan_is_representable": 2 * 5 + 2 * 10 + 2 * 15 == 60,
    }
    manifest = {
        "fixture_id": FIXTURE_ID,
        "case_id": "TQ-PQ-001",
        "database": {
            "sqlite_user_version": user_version,
            "schema_sha256": hashlib.sha256(schema.encode("utf-8")).hexdigest(),
            "source_path": str(source) if source else None,
            "source_sha256": _sha256(source) if source else None,
            "fixture_sha256": _sha256(database),
        },
        "question_ids": list(QUESTION_IDS),
        "questions": questions,
        "constraint_checks": checks,
        "validation_passed": all(checks.values()),
    }
    manifest["manifest_sha256"] = _canonical_hash(manifest)
    if not manifest["validation_passed"]:
        failed = [name for name, passed in checks.items() if not passed]
        raise ValueError(f"TQ-PQ-001 fixture validation failed: {failed}")
    return manifest

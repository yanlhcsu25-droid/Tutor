"""Build and validate the real-question, isolated TQ-PQ-002 fixture."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from uuid import UUID

FIXTURE_ID = "question_bank/chapter3_rich_v1"
QUESTION_IDS = (
    # Six choices.
    "4306626e-8db8-40f2-8951-890153a66049", "53d4da2b-30a6-4207-97c4-9fd3316bb1b1",
    "5ce7a878-181d-4e18-b2d4-d91e24c22b5d", "863021c6-c71f-4fc4-ac0d-da8cfbdc45be",
    "e272d22c-9897-4d89-8cbd-1d7180b32910", "7838b2b1-06bc-4b62-9dd8-e0e72804c3b9",
    # Six fills.
    "53fe22ca-f38e-4867-acf9-712136dc895e", "eb8dd0ee-0b81-4d34-8417-be135ce550cc",
    "3abc1778-90bd-4c32-9678-f2cf0113ca25", "77904b1b-cd9b-41b4-83c3-4980dc97fc99",
    "784fb46e-3bf8-461c-a16e-b89fbfb6f3af", "906a8bdf-9b54-4b77-921e-8da01bd37b3b",
    # Six canonical calculations (解答题 is the same business type).
    "06b9f714-07cc-44c2-9c1c-3928494292b2", "2828e20b-1cdd-4d3c-b45e-3a5f2a212b0d",
    "a1ff67c2-d909-4407-b8f6-f6c4007e9fb9", "b59244fe-e93a-475c-9c86-8a4d8602d2a9",
    "cbc94158-4e12-4b39-860d-86a78539aa48", "e3bf8c72-a43a-4e74-99ab-8d94802fa0c6",
    # Six proofs.
    "1c715445-7eb0-43fc-9860-c0f414f14c21", "0914761c-8b7a-409a-b2a6-72872d1fd389",
    "6194e288-d34b-415a-9820-891aae486d19", "85dcdcaa-e2a6-423b-878b-cb34dec5c4cd",
    "94606222-bc1a-4c15-9e97-902000e42bd7", "e2e07db8-31e9-464f-a72e-79f82ae3cca7",
)
REQUIRED_CANONICAL_TYPES = {"选择题": 4, "填空题": 4, "计算题": 4, "证明题": 2}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_pq002_fixture(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
        with sqlite3.connect(destination) as target:
            original.backup(target)
            marks = ",".join("?" for _ in QUESTION_IDS)
            target.execute(f"UPDATE question SET is_active=0 WHERE id NOT IN ({marks})", QUESTION_IDS)
            target.commit()
    return validate_pq002_fixture(destination, source=source)


def validate_pq002_fixture(database: Path, *, source: Path | None = None) -> dict:
    marks = ",".join("?" for _ in QUESTION_IDS)
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        user_version = db.execute("PRAGMA user_version").fetchone()[0]
        schema = "\n".join(row[0] or "" for row in db.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name"
        ))
        rows = db.execute(f"""
            WITH latest AS (SELECT question_id,MAX(profile_version) v FROM question_profile
                            WHERE profile_status='approved' GROUP BY question_id)
            SELECT q.id question_id,q.draft_id,d.source_name,d.source_item_id,c.textbook_id,
                   q.curriculum_chapter_id chapter_id,c.code chapter_code,c.title chapter_title,
                   q.question_type,q.difficulty raw_question_difficulty,p.difficulty,p.profile_version,
                   p.profile_source,p.profile_status,p.estimated_time_min,q.default_score,
                   q.verification_status,q.review_status,q.is_active,q.knowledge_match_status,
                   q.publish_source,q.question_text
            FROM question q JOIN question_draft d ON d.id=q.draft_id
            JOIN curriculum_node c ON c.id=q.curriculum_chapter_id
            JOIN latest l ON l.question_id=q.id JOIN question_profile p ON p.question_id=l.question_id AND p.profile_version=l.v
            WHERE q.id IN ({marks}) ORDER BY q.question_type,q.id
        """, QUESTION_IDS).fetchall()
        links = db.execute(f"""
            SELECT l.question_id,k.id,k.name,k.curriculum_node_id,cn.textbook_id
            FROM question_knowledge_link l JOIN knowledge_node k ON k.id=l.knowledge_node_id
            JOIN curriculum_node cn ON cn.id=k.curriculum_node_id
            WHERE l.question_id IN ({marks}) ORDER BY l.question_id,k.id
        """, QUESTION_IDS).fetchall()
        raw_links = db.execute(
            f"SELECT COUNT(*) FROM question_knowledge_link WHERE question_id IN ({marks})", QUESTION_IDS
        ).fetchone()[0]
        active_ids = {row[0] for row in db.execute("""
            SELECT q.id FROM question q JOIN question_draft d ON d.id=q.draft_id
            WHERE q.is_active=1 AND q.review_status='approved' AND q.knowledge_match_status='current'
              AND d.source_name NOT IN ('CMM-Math','built-in-demo','test_source')
        """)}

    knowledge = {question_id: [] for question_id in QUESTION_IDS}
    for question_id, node_id, name, curriculum_node_id, textbook_id in links:
        knowledge[question_id].append({"id": node_id, "name": name, "curriculum_node_id": curriculum_node_id, "textbook_id": textbook_id})
    questions = []
    for row in rows:
        item = dict(row)
        item["question_text_sha256"] = hashlib.sha256(item.pop("question_text").encode()).hexdigest()
        item["knowledge_points"] = knowledge[item["question_id"]]
        questions.append(item)
    supply = Counter(item["question_type"] for item in questions)
    checks = {
        "all_questions_present": len(rows) == len(QUESTION_IDS) and {r["question_id"] for r in rows} == set(QUESTION_IDS),
        "question_ids_are_uuid": all(str(UUID(question_id)) == question_id for question_id in QUESTION_IDS),
        "single_textbook": len({r["textbook_id"] for r in rows}) == 1,
        "third_chapter_only": bool(rows) and all(r["chapter_code"] == "三" for r in rows),
        "canonical_supply_exceeds_requirements": all(supply[name] > count for name, count in REQUIRED_CANONICAL_TYPES.items()),
        "approved_current_active": all(r["review_status"] == "approved" and r["knowledge_match_status"] == "current" and r["is_active"] == 1 for r in rows),
        "approved_difficulty_available": all(r["profile_status"] == "approved" and r["difficulty"] in {1,2,3,4,5} for r in rows),
        "knowledge_links_valid": len(links) == raw_links and all(knowledge[q] for q in QUESTION_IDS) and all(
            link["textbook_id"] == item["textbook_id"] for item in questions for link in item["knowledge_points"]
        ),
        "candidate_pool_is_exact": active_ids == set(QUESTION_IDS),
        "question_texts_are_distinct": len({r["question_text_sha256"] for r in questions}) == len(questions),
    }
    manifest = {
        "fixture_id": FIXTURE_ID, "case_id": "TQ-PQ-002",
        "database": {"sqlite_user_version": user_version, "schema_sha256": hashlib.sha256(schema.encode()).hexdigest(),
                     "source_path": str(source) if source else None, "source_sha256": _sha(source) if source else None,
                     "fixture_sha256": _sha(database)},
        "alias_contract": {"解答题": "计算题"},
        "required_canonical_question_type_counts": REQUIRED_CANONICAL_TYPES,
        "fixture_question_type_counts": dict(supply), "question_ids": list(QUESTION_IDS),
        "questions": questions, "constraint_checks": checks, "validation_passed": all(checks.values()),
    }
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest["manifest_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    if not manifest["validation_passed"]:
        raise ValueError(f"TQ-PQ-002 fixture validation failed: {[k for k,v in checks.items() if not v]}")
    return manifest

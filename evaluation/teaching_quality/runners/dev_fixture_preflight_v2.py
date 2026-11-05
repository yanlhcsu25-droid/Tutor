"""Read-only Teaching Quality Dev question-bank preflight (v2).

Fixes chapter-code detection and recognizes the existing 解答题 -> 计算题 alias.
No fixtures are written and no agent/judge/blind data are loaded.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

CANONICAL_TYPES = ("选择题", "填空题", "计算题", "证明题")
PREFIXES = ("第一章", "第二章", "第三章", "第四章")
DEV_PAPER_IDS = (
    "TQ-PQ-001", "TQ-PQ-002", "TQ-PQ-003", "TQ-PQ-010", "TQ-PQ-011",
    "TQ-PQ-012", "TQ-PQ-016", "TQ-PQ-017", "TQ-PQ-020",
)


def get_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")')}


def scalar(db: sqlite3.Connection, sql: str, args: tuple = ()) -> int:
    return int(db.execute(sql, args).fetchone()[0])


def chapter_prefix(code: str | None, title: str | None) -> str | None:
    for raw in (code, title):
        value = str(raw or "").strip()
        match = re.match(r"^(?:第\s*)?([一二三四]|0?[1-4])\s*章(?:\b|\s|$|[^\w])", value)
        if not match and value in {"一", "二", "三", "四", "1", "2", "3", "4", "01", "02", "03", "04"}:
            match_value = value
        elif match:
            match_value = match.group(1)
        else:
            continue
        ordinal = {"一": 1, "二": 2, "三": 3, "四": 4}.get(match_value, int(match_value) if match_value.isdigit() else -1)
        if 1 <= ordinal <= 4:
            return PREFIXES[ordinal - 1]
    return None


def check_preflight(db_path: Path) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"question", "curriculum_node", "question_profile", "question_knowledge_link", "knowledge_node"}
        if required - tables:
            raise ValueError("Missing required tables: " + ", ".join(sorted(required - tables)))
        qcols = get_columns(db, "question")
        ccols = get_columns(db, "curriculum_node")
        if not {"id", "curriculum_chapter_id", "question_type", "is_active", "review_status"} <= qcols:
            raise ValueError("Missing question columns")
        if not {"id", "title", "code", "node_type", "textbook_id"} <= ccols:
            raise ValueError("Missing curriculum_node columns")

        eligibility = "q.is_active = 1 AND q.review_status = 'approved' AND q.question_type IN ('选择题','填空题','计算题','证明题')"
        question_total = scalar(db, "SELECT COUNT(*) FROM question")
        eligible = scalar(db, f"SELECT COUNT(*) FROM question q WHERE {eligibility}")
        state_counts = [dict(r) for r in db.execute(
            "SELECT review_status, is_active, question_type, COUNT(*) AS n FROM question "
            "GROUP BY review_status, is_active, question_type ORDER BY review_status, is_active, question_type"
        )]

        active_textbook = None
        textbook_status = "NO_ACTIVE_TEXTBOOK"
        if "textbook" in tables and {"id", "name", "is_active"} <= get_columns(db, "textbook"):
            active_rows = [dict(r) for r in db.execute("SELECT id,name FROM textbook WHERE is_active=1 ORDER BY id")]
            if len(active_rows) == 1:
                active_textbook = active_rows[0]
                textbook_status = "ONE_ACTIVE_TEXTBOOK"
            elif len(active_rows) > 1:
                textbook_status = "MULTIPLE_ACTIVE_TEXTBOOKS"

        # Only direct materialized owning-chapter links are counted as chapter supply.
        # Chapter number is stored in curriculum_node.code, not necessarily in title.
        chapter_records = [dict(r) for r in db.execute(
            "SELECT c.id, c.code, c.title, c.node_type, c.textbook_id, q.question_type, COUNT(*) AS n "
            "FROM question q LEFT JOIN curriculum_node c ON c.id=q.curriculum_chapter_id "
            f"WHERE {eligibility} "
            "GROUP BY c.id,c.code,c.title,c.node_type,c.textbook_id,q.question_type "
            "ORDER BY c.title,q.question_type"
        )]
        named = {prefix: {"total": 0, "types": {t: 0 for t in CANONICAL_TYPES}, "matched_chapter_titles": []} for prefix in PREFIXES}
        uncategorized = Counter()
        discovered_chapters = {}
        other_textbooks = 0
        for r in chapter_records:
            n = r["n"]
            if active_textbook and r["textbook_id"] not in (None, active_textbook["id"]):
                other_textbooks += n
                continue
            prefix = chapter_prefix(r["code"], r["title"]) if r["node_type"] == "chapter" else None
            if prefix is None:
                reason = "NO_CHAPTER_LINK" if r["id"] is None else "NON_CHAPTER_OR_UNRECOGNIZED_CODE"
                uncategorized[reason] += n
                continue
            named[prefix]["total"] += n
            named[prefix]["types"][r["question_type"]] += n
            chapter_key = (r["id"], r["code"], r["title"])
            discovered_chapters[chapter_key] = prefix
            label = f'{r["code"] or ""} {r["title"] or ""}'.strip()
            if label not in named[prefix]["matched_chapter_titles"]:
                named[prefix]["matched_chapter_titles"].append(label)

        available_profiles = scalar(db, f"SELECT COUNT(DISTINCT p.question_id) FROM question_profile p JOIN question q ON q.id=p.question_id WHERE {eligibility}")
        available_links = scalar(db, f"SELECT COUNT(DISTINCT l.question_id) FROM question_knowledge_link l JOIN question q ON q.id=l.question_id WHERE {eligibility}")
        topic_counts = {}
        for term in ("极限", "高阶导数", "洛必达"):
            topic_counts[term] = scalar(db,
                "SELECT COUNT(DISTINCT q.id) FROM question q "
                "JOIN question_knowledge_link l ON l.question_id=q.id "
                "JOIN knowledge_node k ON k.id=l.knowledge_node_id "
                f"WHERE {eligibility} AND k.name LIKE ?", (f"%{term}%",))
        difficulty = [dict(r) for r in db.execute(
            "SELECT p.difficulty,COUNT(DISTINCT q.id) n FROM question q "
            "JOIN question_profile p ON p.question_id=q.id "
            "AND p.profile_version=(SELECT MAX(p2.profile_version) FROM question_profile p2 WHERE p2.question_id=q.id) "
            f"WHERE {eligibility} GROUP BY p.difficulty ORDER BY p.difficulty"
        )]
        c1, c2, c3, c4 = [named[p]["total"] for p in PREFIXES]
        req = {"选择题": 4, "填空题": 4, "计算题": 4, "证明题": 2}
        chapter3_types = named["第三章"]["types"]
        shortages = {t: req[t] - chapter3_types[t] for t in req if chapter3_types[t] < req[t]}

        def row(case: str, status: str, rationale: str) -> dict:
            return {"case_id": case, "preflight": status, "reason": rationale}

        paper_cases = [
            row("TQ-PQ-001", "SUPPLY_MINIMUM_MET" if c1 >= 6 else "SUPPLY_INSUFFICIENT", f"第一章可用题数{c1}，最低需要6；未审查重复/难度"),
            row("TQ-PQ-002", "SUPPLY_MINIMUM_MET" if not shortages else "SUPPLY_INSUFFICIENT",
                f"解答题已归并计算题；第三章最低需求{req}，可用{chapter3_types}；缺口{shortages}；未验证组卷可行性"),
            row("TQ-PQ-003", "SUPPLY_MINIMUM_MET" if all((c1, c2, c3, c4)) else "SUPPLY_INSUFFICIENT",
                f"一至四章可用题数={c1}/{c2}/{c3}/{c4}；待检查默认蓝图"),
            row("TQ-PQ-010", "NEEDS_METHOD_REVIEW", f"带极限知识关联题{topic_counts['极限']}；须审查是否使用洛必达"),
            row("TQ-PQ-011", "NEEDS_FIXED_GROUP_MAPPING", "需人工确认Q01/Q07/Q12及Q02/Q09对应的相似题分组，UUID不可任意映射"),
            row("TQ-PQ-012", "NEEDS_METHOD_REVIEW", f"带极限知识关联题{topic_counts['极限']}；须确认至少3种不同方法"),
            row("TQ-PQ-016", "NEEDS_SCORE_SHARE_REVIEW", f"带高阶导数知识关联题{topic_counts['高阶导数']}；须检查占分≥60%"),
            row("TQ-PQ-017", "SUPPLY_MINIMUM_MET" if all((c1, c2, c3)) else "SUPPLY_INSUFFICIENT",
                f"一至三章可用题数={c1}/{c2}/{c3}；待审查难度梯度"),
            row("TQ-PQ-020", "SUPPLY_MINIMUM_MET" if all((c1, c2, c3)) else "SUPPLY_INSUFFICIENT",
                f"一至三章可用题数={c1}/{c2}/{c3}；待审查合理比例"),
        ]
        return {
            "kind": "teaching_quality_dev_fixture_readonly_preflight_v2",
            "official_benchmark": False, "model_calls": 0, "blind_executed": False,
            "source_db_read_only": True,
            "counts": {"question_total": question_total, "approved_active_supported_type": eligible,
                       "eligible_with_any_question_profile": available_profiles,
                       "eligible_with_any_knowledge_link": available_links},
            "textbook_status": textbook_status, "active_textbook": active_textbook,
            "eligible_from_other_textbooks": other_textbooks,
            "unmapped_chapter_count": dict(uncategorized),
            "question_state_counts": state_counts,
            "chapter_prefix_summary": named,
            "discovered_chapters": [{"id": i, "code": code, "title": title, "prefix": prefix}
                                   for (i, code, title), prefix in discovered_chapters.items()],
            "topic_link_counts_approximate": topic_counts,
            "latest_profile_difficulty_counts": difficulty,
            "dev_paper_cases": paper_cases,
            "limitations": [
                "Chapter supply uses direct question.curriculum_chapter_id; unassigned questions are not inferred or counted",
                "Active textbook isolation applies where identifiable; no fixture data is inserted",
                "Minimum supplies alone do not prove content, difficulty, grouping, scoring, or agent task success",
            ],
        }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="calculus_agent.db")
    p.add_argument("--output", default="evaluation/artifacts/reports/teaching_quality/runs/dev_fixture_preflight_v2.json")
    args = p.parse_args()
    info = check_preflight(Path(args.db))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT=" + str(out))
    print("QUESTION_TOTAL=" + str(info["counts"]["question_total"]))
    print("ELIGIBLE=" + str(info["counts"]["approved_active_supported_type"]))
    print("PROFILED=" + str(info["counts"]["eligible_with_any_question_profile"]))
    print("KNOWLEDGE_LINKED=" + str(info["counts"]["eligible_with_any_knowledge_link"]))
    print("ACTIVE_TEXTBOOK=" + str(info["active_textbook"]))
    print("CHAPTER_UNMAPPED=" + str(info["unmapped_chapter_count"]))
    print("OTHER_TEXTBOOK_QUESTIONS=" + str(info["eligible_from_other_textbooks"]))
    for name, record in info["chapter_prefix_summary"].items():
        print(name, record["total"], record["types"], record["matched_chapter_titles"])
    for case in info["dev_paper_cases"]:
        print(case["case_id"], case["preflight"], case["reason"])
    print("MODEL_CALLS=0 BLIND_EXECUTED=False DB_READ_ONLY=True")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""V4 isolated REAL-provider contextual-question acceptance test for Teacher Agent.

Runs seven black-box cases against FastAPI TestClient in a SQLite backup of the
project DB. Uses the configured *real* Teacher Agent backend when --live is given.
Does NOT modify the source database, benchmark, graders, oracle or fixtures.

Example:
  uv run python tests/evals/contextual_question_live_validation.py --db calculus_agent.db
  uv run python tests/evals/contextual_question_live_validation.py --db calculus_agent.db --live

Needs an existing Paper template with global positions 7 and 8 and >=2
calculation questions, regardless of status. The script clones this paper and its
items into a brand-new DRAFT inside an isolated SQLite backup only.
Optional --paper-id selects the template, not the paper to modify.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_PREFIX = "/api/v1/teacher-agent"
CASE_IDS = ("T01", "T02", "T03", "T04", "T05", "T06", "T07")
CASE_SPEC = {
    "T01": {"negative": "把这道题换掉"},
    "T02": {"negative": "换一下刚才那题"},
    "T03": {"negative": "删除一道计算题"},
    "T04": {"read": 7, "action": "把这道题换掉", "type": "replace_question"},
    "T05": {"read": 8, "action": "换一下刚才那题", "type": "replace_question"},
    "T06": {"read": 7, "action": "删除刚才那题", "type": "remove_question"},
    "T07": {"read": 7, "action": "把这道题改成12分", "type": "score_update", "score": 12},
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", type=Path, default=Path.cwd(), help="Project root (default: cwd)")
    p.add_argument("--db", type=Path, default=Path("calculus_agent.db"), help="Existing SQLite DB, relative to repo")
    p.add_argument("--paper-id", default=None, help="Existing paper to COPY as draft in isolated DB; no source changes")
    p.add_argument("--cases", nargs="+", choices=CASE_IDS, default=list(CASE_IDS))
    p.add_argument("--output-dir", type=Path, default=Path("tests/evals/reports"))
    p.add_argument("--live", action="store_true", help="Explicitly allow configured real model API calls")
    p.add_argument("--keep-clone", action="store_true", help="Keep cloned database after the test")
    return p.parse_args(argv)


def plain(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def db_connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=15)
    con.row_factory = sqlite3.Row
    return con


def get_tables(con: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def get_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in con.execute(f'PRAGMA table_info("{table}")')}


def papers_available(con: sqlite3.Connection) -> list[dict[str, Any]]:
    tables = get_tables(con)
    if not {"paper", "paper_item"} <= tables:
        raise RuntimeError(f"Expected 'paper' and 'paper_item' SQLite tables; found: {sorted(tables)[:35]}")
    pcols = get_columns(con, "paper")
    sort = "p.created_at DESC" if "created_at" in pcols else "p.rowid DESC"
    rows = con.execute(f"""
        SELECT p.id, {('p.status' if 'status' in pcols else "'unknown'")} AS status,
               {('p.version' if 'version' in pcols else '1')} AS version,
               count(i.id) AS question_count,
               SUM(CASE WHEN i.section LIKE '%计算%' THEN 1 ELSE 0 END) AS calculation_count,
               SUM(CASE WHEN i.position = 7 THEN 1 ELSE 0 END) AS has_7,
               SUM(CASE WHEN i.position = 8 THEN 1 ELSE 0 END) AS has_8
        FROM paper p LEFT JOIN paper_item i ON i.paper_id = p.id
        GROUP BY p.id ORDER BY {sort} LIMIT 1500
    """).fetchall()
    return [plain(row) for row in rows]


def compatible(p: dict[str, Any]) -> bool:
    """Template may be published/confirmed; only the isolated copy is made draft."""
    return (p["question_count"] >= 10 and p["calculation_count"] >= 2
            and p["has_7"] == 1 and p["has_8"] == 1)


def choose_paper(con: sqlite3.Connection, explicit: str | None) -> dict[str, Any]:
    options = papers_available(con)
    if explicit:
        found = next((p for p in options if p["id"] == explicit), None)
        if found is None:
            raise RuntimeError(f"Paper {explicit!r} not found in DB")
        if not compatible(found):
            raise RuntimeError(f"Paper cannot be used as a template for T01-T07: {found}")
        return found
    matched = [p for p in options if compatible(p)]
    if not matched:
        listing = [dict(id=p["id"], questions=p["question_count"], calc=p["calculation_count"])
                   for p in options[:12]]
        raise RuntimeError("No compatible paper template. Need >=10 questions, positions 7/8, >=2 calculation questions. "
                           f"Recent papers: {listing}. Choose a seeded test DB or --paper-id.")
    # Prefer a 10-question paper, matching the original EVAL fixture layout.
    return next((p for p in matched if p["question_count"] == 10), matched[0])


def _insert_clone(con: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    """Clone rows with schema-neutral column binding (table names are fixed constants)."""
    keys = list(values)
    column_names = ", ".join('"' + k.replace('"', '""') + '"' for k in keys)
    placeholders = ", ".join("?" for _ in keys)
    con.execute(f'INSERT INTO "{table}" ({column_names}) VALUES ({placeholders})',
                tuple(values[k] for k in keys))


def make_isolated_draft(con: sqlite3.Connection, template_id: str) -> dict[str, Any]:
    """Create a new paper/items *only in the temporary SQLite backup*.

    Never reuse or mutate a user's source Paper row. This allows existing
    published/confirmed/archived templates to participate in validation.
    """
    template = con.execute('SELECT * FROM "paper" WHERE id = ?', (template_id,)).fetchone()
    if template is None:
        raise RuntimeError(f"Paper template {template_id} not found in backup")
    items = con.execute('SELECT * FROM "paper_item" WHERE paper_id = ? ORDER BY position, id',
                        (template_id,)).fetchall()
    if len(items) < 10:
        raise RuntimeError(f"Template {template_id} lacks 10 question rows")
    draft_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    paper = plain(template)
    paper["id"] = draft_id
    for col in ("root_paper_id", "parent_version_id"):
        if col in paper:
            paper[col] = None
    if "status" in paper:
        paper["status"] = "draft"
    if "version" in paper:
        paper["version"] = 1
    if "validation_status" in paper:
        paper["validation_status"] = "pending"
    if "title" in paper:
        paper["title"] = "Contextual Reference Isolated Validation"
    if "created_at" in paper:
        paper["created_at"] = now
    if "updated_at" in paper:
        paper["updated_at"] = now
    try:
        _insert_clone(con, "paper", paper)
        for item in items:
            cloned_item = plain(item)
            cloned_item["id"] = str(uuid.uuid4())
            cloned_item["paper_id"] = draft_id
            for col in ("created_at", "updated_at"):
                if col in cloned_item:
                    cloned_item[col] = now
            _insert_clone(con, "paper_item", cloned_item)
    except sqlite3.IntegrityError as exc:
        raise RuntimeError(f"Cannot clone this Paper template; SQLite constraint: {exc}") from exc
    con.commit()
    result = next((row for row in papers_available(con) if row["id"] == draft_id), None)
    if result is None or not compatible(result) or result["status"] != "draft":
        raise RuntimeError(f"New isolated draft is invalid: {result}")
    return result


def paper_snapshot(con: sqlite3.Connection, paper_id: str) -> dict[str, Any]:
    # Omit ephemeral timestamp fields and compare the actual version and question content.
    pcols = get_columns(con, "paper")
    icols = get_columns(con, "paper_item")
    wanted_p = [v for v in ("id", "version", "status", "total_score", "parent_version_id", "root_paper_id") if v in pcols]
    wanted_i = [v for v in ("id", "paper_id", "question_id", "section", "position", "score", "locked") if v in icols]
    p = con.execute(f'SELECT {", ".join(wanted_p)} FROM paper WHERE id = ?', (paper_id,)).fetchone()
    items = con.execute(f'SELECT {", ".join(wanted_i)} FROM paper_item WHERE paper_id = ? ORDER BY position,id',
                        (paper_id,)).fetchall()
    all_paper_ids = [r["id"] for r in con.execute("SELECT id FROM paper ORDER BY id")]
    return {"paper": plain(p) if p else None, "items": [plain(i) for i in items],
            "all_paper_ids": all_paper_ids}


def pending_rows(con: sqlite3.Connection, cid: str) -> dict[str, Any]:
    """Observe database rows for this session in pending/plan tables, without schema assumptions."""
    out: dict[str, Any] = {}
    for table in sorted(get_tables(con)):
        low = table.lower()
        if not any(x in low for x in ("pending", "change_plan", "paper_change", "adjustment")):
            continue
        if "conversation_id" not in get_columns(con, table):
            continue
        # A table name is obtained from sqlite_master; quote it defensively.
        quoted = '"' + table.replace('"', '""') + '"'
        try:
            rows = con.execute(f"SELECT * FROM {quoted} WHERE conversation_id = ?", (cid,)).fetchall()
            if rows:
                out[table] = [plain(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            out[table] = {"read_error": str(exc)}
    return out


def tool_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    raw = trace.get("tool_calls_json") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return raw if isinstance(raw, list) else []


def tool_name(c: dict[str, Any]) -> str:
    return str(c.get("tool_name") or c.get("name") or c.get("tool") or "")


def find_calls(trace: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [c for c in tool_calls(trace) if tool_name(c) == name]


def extract_question(trace: dict[str, Any], position: int) -> dict[str, Any] | None:
    """Capture question identity from actual read_paper tool observation, not model response."""
    for call in find_calls(trace, "read_paper"):
        result = call.get("result") or {}
        if not isinstance(result, dict) or not result.get("ok", False):
            continue
        questions = result.get("questions") or []
        matches = [q for q in questions if isinstance(q, dict) and q.get("position") == position]
        if len(matches) == 1 and matches[0].get("question_id"):
            return matches[0]
    return None


def extract_operation(trace: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    calls = find_calls(trace, "preview_paper_changes")
    operations: list[dict[str, Any]] = []
    for call in calls:
        result = call.get("result") or {}
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        plan = result.get("plan") or {}
        if isinstance(plan, dict):
            operations.extend(op for op in plan.get("operations", []) if isinstance(op, dict))
    return operations, len(calls)


def model_stats(trace: dict[str, Any]) -> dict[str, Any]:
    """Best effort: missing provider usage is null, not invented zero."""
    spans = trace.get("spans") or []
    relevant = []
    for s in spans:
        if not isinstance(s, dict):
            continue
        ident = str(s.get("span_type", "") + " " + s.get("name", "")).lower()
        if any(x in ident for x in ("llm", "model", "backend.complete")):
            relevant.append(s)
    tin, tout = [], []
    for s in relevant:
        data = s.get("output_json") or {}
        if not isinstance(data, dict):
            continue
        usage = data.get("usage") or data.get("token_usage") or {}
        if isinstance(usage, dict):
            a = usage.get("input_tokens", usage.get("prompt_tokens"))
            b = usage.get("output_tokens", usage.get("completion_tokens"))
            if isinstance(a, int):
                tin.append(a)
            if isinstance(b, int):
                tout.append(b)
    return {"model_spans": len(relevant) if relevant else None,
            "input_tokens": sum(tin) if tin else None,
            "output_tokens": sum(tout) if tout else None}


def call_api(client: Any, cid: str, paper_id: str, message: str) -> dict[str, Any]:
    started = time.monotonic()
    resp = client.post(BASE_PREFIX + "/run", json={"message": message, "conversation_id": cid,
                                                   "paper_id": paper_id}, timeout=180)
    elapsed = round((time.monotonic() - started) * 1000)
    if resp.status_code != 200:
        raise RuntimeError(f"POST /run HTTP {resp.status_code}: {resp.text[:1500]}")
    result = resp.json()
    run_id = result.get("run_id")
    if not run_id:
        raise RuntimeError(f"Run response lacks run_id; cannot verify tool trace: {str(result)[:1000]}")
    tr = client.get(f"{BASE_PREFIX}/runs/{run_id}", timeout=30)
    if tr.status_code != 200:
        raise RuntimeError(f"Cannot fetch trace {run_id}: HTTP {tr.status_code}: {tr.text[:500]}")
    return {"message": message, "response": result, "trace": tr.json(), "elapsed_ms": elapsed}


def check_case(case_id: str, spec: dict[str, Any], turns: list[dict[str, Any]],
               before: dict[str, Any], after: dict[str, Any],
               pending_before: dict[str, Any], pending_after: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    errors: list[str] = []
    facts: dict[str, Any] = {}
    final = turns[-1]
    res, tr = final["response"], final["trace"]
    status = res.get("status")
    facts["status"] = status
    facts["tools"] = [tool_name(t) for t in tool_calls(tr)]
    facts["paper_unchanged"] = before == after
    if before != after:
        errors.append("Original Paper/PaperItem changed without Confirm")
    if "negative" in spec:
        if status != "needs_clarification":
            errors.append(f"Expected needs_clarification, got {status!r}")
        operations, preview_call_count = extract_operation(tr)
        if preview_call_count:
            errors.append(f"Negative case invoked preview_paper_changes {preview_call_count} time(s)")
        if res.get("adjustment_preview") or res.get("replacement_preview"):
            errors.append("Negative case created a modification preview")
        if pending_after != pending_before:
            errors.append("Negative case changed conversation-scoped Pending DB rows")
    else:
        read = turns[0]
        read_status = read["response"].get("status")
        if read_status != "completed":
            errors.append(f"First turn read not completed: {read_status!r}")
        question = extract_question(read["trace"], spec["read"])
        facts["read_question"] = question
        if question is None:
            errors.append(f"First turn lacks read_paper observation for global position {spec['read']}")
        if status != "waiting_confirmation":
            errors.append(f"Expected waiting_confirmation, got {status!r}")
        operations, preview_call_count = extract_operation(tr)
        facts["preview_operations"] = operations
        if preview_call_count != 1:
            errors.append(f"Expected one preview_paper_changes call, got {preview_call_count}")
        if len(operations) != 1:
            errors.append(f"Expected one successful preview operation, got {len(operations)}")
        else:
            op = operations[0]
            if op.get("position") != spec["read"]:
                errors.append(f"Preview position {op.get('position')} != {spec['read']}")
            if question and op.get("old_question_id") != question["question_id"]:
                errors.append("Preview old_question_id does not match read_paper question_id")
            if spec["type"] == "score_update":
                if op.get("type") != "change_score":
                    errors.append(f"Unexpected score operation type {op.get('type')!r}")
                if op.get("score_after") != spec["score"]:
                    errors.append(f"Expected score_after={spec['score']}, got {op.get('score_after')}")
            elif op.get("type") != spec["type"]:
                errors.append(f"Expected operation {spec['type']!r}, got {op.get('type')!r}")
        if not res.get("adjustment_preview") and not operations:
            errors.append("No preview evidence in result or trace")
        # The Pending contract is part of PASS, not merely a tool-side preview.
        # A different schema without conversation_id must be reported as unverified.
        facts["pending_db_observed"] = bool(pending_after)
        if not pending_after:
            errors.append("No conversation-scoped Pending row found in the isolated DB; persistence NOT verified")
    facts["pending_tables_before"] = sorted(pending_before)
    facts["pending_tables_after"] = sorted(pending_after)
    facts["last_turn_model_usage"] = model_stats(tr)
    return not errors, errors, facts


def _structured_value(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    return value


def pending_plan_ids(pending: dict[str, Any]) -> set[str]:
    """Extract plan_id even when Pending rows carry JSON encoded payloads."""
    ids: set[str] = set()

    def walk(v: Any, depth: int = 0) -> None:
        if depth > 12:
            return
        v = _structured_value(v)
        if isinstance(v, dict):
            plan_id = v.get("plan_id")
            if isinstance(plan_id, str) and plan_id:
                ids.add(plan_id)
            for x in v.values():
                walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)

    walk(pending)
    return ids


def final_preview_plan_id(response: dict[str, Any], call: dict[str, Any]) -> str | None:
    for preview in (response.get("adjustment_preview"), response.get("replacement_preview"), call.get("result")):
        preview = _structured_value(preview)
        if not isinstance(preview, dict):
            continue
        plan = _structured_value(preview.get("plan"))
        if isinstance(plan, dict) and isinstance(plan.get("plan_id"), str):
            return plan["plan_id"]
    return None


def preview_target_is_expected(
    preview_call: dict[str, Any],
    expected_position: int,
    expected_question: dict[str, Any],
    paper_snapshot_before: dict[str, Any],
) -> bool:
    """Check requested address against pre-turn Paper order, not model text."""
    args = _structured_value(preview_call.get("arguments"))
    if not isinstance(args, dict):
        return False
    operations = args.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        return False
    op = operations[0]
    if not isinstance(op, dict) or op.get("type") != "replace_question":
        return False
    target = op.get("target")
    if not isinstance(target, dict):
        return False
    if target.get("position") is not None:
        return target["position"] == expected_position
    if target.get("question_id") is not None:
        return target["question_id"] == expected_question.get("question_id")
    typ, number = target.get("section_type"), target.get("section_order")
    if not isinstance(typ, str) or not isinstance(number, int) or number < 1:
        return False
    items = paper_snapshot_before.get("items") or []
    selected = sorted(
        (item for item in items if isinstance(item, dict) and item.get("section") == typ),
        key=lambda i: i.get("position", 0),
    )
    return len(selected) >= number and selected[number - 1].get("position") == expected_position


def check_case_semantic_v4(
    case_id: str,
    spec: dict[str, Any],
    turns: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
    pending_before: dict[str, Any],
    pending_after: dict[str, Any],
    strict_pass: bool,
    strict_errors: list[str],
) -> tuple[bool, list[str], dict[str, Any]]:
    """Keep original strict contract and add V4's correct candidate-exhaustion branch.

    T05 may legitimately return needs_clarification when zero scoped candidates
    exist; it must not create a Pending or loop until tool_round_limit.
    If a valid candidate exists, T05 must instead meet the original success contract.
    """
    res = turns[-1]["response"]
    trace = turns[-1]["trace"]
    status = res.get("status")
    previews = find_calls(trace, "preview_paper_changes")
    facts = {"semantic_contract_version": "v4", "preview_call_count": len(previews)}
    errors = []
    if before != after:
        errors.append("Original Paper/PaperItem changed without Confirm")
    if case_id != "T05" or status == "waiting_confirmation":
        errors.extend(strict_errors)
        # Verify persistence relation explicitly on success (the legacy script
        # checked only for any Pending row, not that it matches the final plan).
        if not errors and "negative" not in spec:
            final_plan_id = final_preview_plan_id(res, previews[-1]) if previews else None
            ids = pending_plan_ids(pending_after)
            facts.update(final_plan_id=final_plan_id, pending_plan_ids=sorted(ids))
            if not final_plan_id or final_plan_id not in ids:
                errors.append("Final successful Preview plan_id is not linked to persisted Pending")
        return not errors, errors, facts

    # T05: no matching candidate under existing Paper constraints.
    if status != "needs_clarification":
        errors.append(f"T05 expected needs_clarification on candidate exhaustion, got {status!r}")
    read_status = turns[0]["response"].get("status")
    if read_status != "completed":
        errors.append(f"First-turn read should complete, got {read_status!r}")
    question = extract_question(turns[0]["trace"], spec["read"])
    facts["read_question"] = question
    if question is None:
        errors.append("No first-turn read_paper question identity")
    if len(previews) != 1:
        errors.append(f"Expected exactly one failed Preview, got {len(previews)}")
    if previews:
        failed = previews[0].get("result") or {}
        if not isinstance(failed, dict) or failed.get("ok") is not False:
            errors.append("T05 candidate-exhaustion Preview should return ok=False")
        plan = failed.get("plan") or {}
        blockers = failed.get("blocking_errors") or (plan.get("blocking_errors") if isinstance(plan, dict) else []) or []
        facts["tool_blocking_errors"] = blockers
        if "same_difficulty_candidate_not_found" not in blockers:
            errors.append("Missing deterministic same_difficulty_candidate_not_found evidence")
        if question is not None and not preview_target_is_expected(previews[0], spec["read"], question, before):
            errors.append("Failed Preview targeted a different question than first-turn read_paper")
    if pending_after != pending_before:
        errors.append("Candidate exhaustion created or changed Pending")
    if res.get("adjustment_preview"):
        result_preview = res.get("adjustment_preview")
        if isinstance(result_preview, dict) and result_preview.get("ok") is True:
            errors.append("Candidate exhaustion returned a successful adjustment preview")
    text = str(res.get("message") or "").strip()
    facts["user_message"] = text
    if not text or ("稍后重试" in text and not any(k in text for k in ("难度", "候选", "题库", "题目"))):
        errors.append("Clarification message does not explain the candidate limitation")
    if "agent_tool_round_limit" in (res.get("blocking_errors") or []):
        errors.append("Agent still hit agent_tool_round_limit")
    return not errors, errors, facts


def git_head(repo: Path) -> str | None:
    try:
        cmd = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                             text=True, timeout=5, check=True)
        return cmd.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def write_reports(data: dict[str, Any], outdir: Path) -> tuple[Path, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"contextual-question-reference-api-validation-v4-{stamp}"
    jp, mp = outdir / f"{stem}.json", outdir / f"{stem}.md"
    # Exclusive creation: never overwrite frozen or previous reports.
    with jp.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    lines = ["# Teacher Agent · Contextual Question Reference Live API Validation V4", "",
             f"- UTC: {data['timestamp_utc']}", f"- Git HEAD: `{data['git_head']}`",
             f"- Source SQLite: `{data['source_db']}` (read only)",
             f"- Test paper ID: `{data['paper_id']}`",
             "- Running mode: isolated FastAPI API router + TestClient / real configured backend",
             f"- V4 business-semantic result: **{data['passed']}/{data['total']} PASS**",
             f"- Original V3 strict contract: **{data['strict_passed']}/{data['total']} PASS**", "",
             "| Case | V4 semantic | Original strict | Final status | V4 errors |", "|---|---|---|---|---|"]
    for c in data["cases"]:
        errs = "; ".join(c["errors"]) or "—"
        lines.append(f"| {c['case_id']} | {'PASS' if c['passed'] else 'FAIL'} | "
                     f"{'PASS' if c.get('strict_passed') else 'FAIL'} | "
                     f"{c.get('facts',{}).get('status','—')} | {errs.replace('|','/')} |")
    lines.extend(["", "Trace、工具参数、前后数据库快照及模型调用情况请见同名 JSON。", "",
                  "该报告为独立定向验收，不改变 Dev117 的 97/117 首测和 104/117 离线复评。", ""])
    with mp.open("x", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return jp, mp


def build_isolated_api(db_url: str, cloned_db: Path):
    """Mount the production API router with EXPLICIT isolated FastAPI dependencies.

    Settings may load database_url from .env or a prefixed variable, so assigning
    os.environ["DATABASE_URL"] is NOT an isolation guarantee. Both the API's
    Settings dependency and its SQLAlchemy Session dependency must point to the
    temporary SQLite copy, irrespective of the user's normal configuration.

    The miniature app deliberately does not import calculus_agent.main or run its
    production lifespan/startup handlers. It uses the SAME teacher-agent route,
    response schemas, business coordinator and real provider factory as the app.
    """
    from fastapi import FastAPI
    from calculus_agent import api as agent_api
    from calculus_agent.config import get_settings
    from calculus_agent.db import build_session_factory

    original_settings = get_settings()
    if not hasattr(original_settings, "model_copy"):
        raise RuntimeError("Current Settings does not support Pydantic v2 model_copy")
    isolated_settings = original_settings.model_copy(update={"database_url": db_url})
    actual_url = str(isolated_settings.database_url)
    expected_url = str(db_url)
    if actual_url != expected_url:
        raise RuntimeError(f"Isolated Settings mismatch: expected {expected_url!r}; got {actual_url!r}")

    factory = build_session_factory(db_url)
    expected_file = cloned_db.resolve(strict=True)

    def verify_bind(session: Any) -> None:
        rows = session.connection().exec_driver_sql("PRAGMA database_list").fetchall()
        db_files = [Path(str(row[2])).resolve() for row in rows if str(row[1]) == "main" and row[2]]
        if db_files != [expected_file]:
            raise RuntimeError(
                "DATABASE ISOLATION FAILURE: API Session did not bind to temporary SQLite. "
                f"Expected {str(expected_file)!r}; got {[str(p) for p in db_files]!r}"
            )

    # Verify the exact SQLAlchemy session factory *before* constructing the app.
    with factory() as check_session:
        verify_bind(check_session)

    def isolated_session():
        with factory.begin() as session:
            verify_bind(session)
            yield session

    def isolated_config():
        return isolated_settings

    app = FastAPI()
    app.include_router(agent_api.router)
    # Override BOTH dependencies, so neither .env nor cached config can route
    # a request to the user's original DB. No global config/production app edits.
    app.dependency_overrides[agent_api.get_settings] = isolated_config
    app.dependency_overrides[agent_api.get_session] = isolated_session
    return app


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    repo = a.repo.expanduser().resolve()
    db = a.db.expanduser()
    db = (repo / db).resolve() if not db.is_absolute() else db.resolve()
    outdir = (repo / a.output_dir).resolve() if not a.output_dir.is_absolute() else a.output_dir.resolve()
    if not db.exists():
        print(f"ERROR: SQLite database does not exist: {db}", file=sys.stderr)
        return 2
    try:
        with db_connect(db) as con:
            p = choose_paper(con, a.paper_id)
    except (sqlite3.DatabaseError, RuntimeError) as exc:
        print(f"PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 2
    print(f"Source DB: {db} (never modified by harness)")
    print(f"Selected template: {p['id']} / status={p['status']} / version={p['version']} / "
          f"questions={p['question_count']} / calculations={p['calculation_count']}")
    print("Scenarios:", ", ".join(a.cases))

    temp = tempfile.TemporaryDirectory(prefix="teacher-agent-contextual-live-")
    clone = Path(temp.name) / "isolated_eval.db"
    try:
        # sqlite.backup handles a WAL database safely without mutating the source DB.
        with db_connect(db) as src, sqlite3.connect(str(clone)) as dst:
            src.backup(dst)
        with db_connect(clone) as isolated:
            draft = make_isolated_draft(isolated, p["id"])
        test_paper_id = draft["id"]
        print(f"Created TEMPORARY draft: {test_paper_id} / status={draft['status']} / "
              f"questions={draft['question_count']} / calculations={draft['calculation_count']}")
        from fastapi.testclient import TestClient
        # Configure and prove isolation before *any* Agent/model request.
        db_url = f"sqlite:///{clone.as_posix()}"
        app = build_isolated_api(db_url, clone)
        results: list[dict[str, Any]] = []
        with TestClient(app) as client:
            h = client.get("/api/v1/health", timeout=15)
            if h.status_code != 200:
                raise RuntimeError(f"Isolated API router health failed: HTTP {h.status_code}: {h.text[:300]}")
            print(f"ISOLATION VERIFIED: route Settings + Session -> {clone}", flush=True)
            if not a.live:
                print("PREFLIGHT PASS (0 model calls). The production API router is bound to an isolated DB. "
                      "Original database unchanged. Add --live to execute real-provider tests.")
                return 0
            for case_id in a.cases:
                spec = CASE_SPEC[case_id]
                cid = f"ctx-live-{case_id.lower()}-{uuid.uuid4().hex[:12]}"
                print(f"[{case_id}] Running real backend", flush=True)
                with db_connect(clone) as con:
                    before = paper_snapshot(con, test_paper_id)
                    pending_before = pending_rows(con, cid)
                row: dict[str, Any] = {"case_id": case_id, "conversation_id": cid,
                                       "spec": spec, "turns": [], "passed": False, "errors": [], "facts": {}}
                try:
                    if "negative" in spec:
                        row["turns"].append(call_api(client, cid, test_paper_id, spec["negative"]))
                    else:
                        row["turns"].append(call_api(client, cid, test_paper_id, f"查看全卷第{spec['read']}题"))
                        row["turns"].append(call_api(client, cid, test_paper_id, spec["action"]))
                    with db_connect(clone) as con:
                        after = paper_snapshot(con, test_paper_id)
                        pending_after = pending_rows(con, cid)
                    strict_pass, strict_errors, strict_facts = check_case(
                        case_id, spec, row["turns"], before, after,
                        pending_before, pending_after,
                    )
                    passed, errors, semantic_facts = check_case_semantic_v4(
                        case_id, spec, row["turns"], before, after,
                        pending_before, pending_after, strict_pass, strict_errors,
                    )
                    facts = {**strict_facts, **semantic_facts}
                    row.update(strict_passed=strict_pass, strict_errors=strict_errors,
                               passed=passed, errors=errors, facts=facts,
                               paper_before=before, paper_after=after,
                               pending_before=pending_before, pending_after=pending_after)
                except Exception as exc:  # Record traceback while preserving remaining cases.
                    import traceback
                    row["errors"] = [f"EXECUTION_ERROR: {type(exc).__name__}: {exc}"]
                    row["exception_traceback"] = traceback.format_exc()
                    with db_connect(clone) as con:
                        row["paper_before"] = before
                        row["paper_after"] = paper_snapshot(con, test_paper_id)
                        row["pending_after"] = pending_rows(con, cid)
                results.append(row)
                print(f"[{case_id}] {'PASS' if row['passed'] else 'FAIL'}: "
                      f"{'; '.join(row['errors']) or 'all assertions satisfied'}", flush=True)
        data = {"suite": "contextual_question_reference_real_api_7_v4",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "git_head": git_head(repo), "source_db": str(db),
                "paper_id": test_paper_id, "template_paper_id": p["id"], "template_status": p["status"], "isolated_db": str(clone),
                "passed": sum(bool(c["passed"]) for c in results),
                "strict_passed": sum(bool(c.get("strict_passed")) for c in results),
                "total": len(results),
                "cases": results, "baseline_unchanged": {"dev117_first": "97/117", "offline_regraded": "104/117"}}
        jp, mp = write_reports(data, outdir)
        print(f"\nV4 BUSINESS RESULT: {data['passed']}/{data['total']} PASS")
        print(f"V3 STRICT CONTRACT: {data['strict_passed']}/{data['total']} PASS")
        print(f"JSON: {jp}\nMarkdown: {mp}")
        return 0 if data["passed"] == data["total"] else 1
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"TEST INFRASTRUCTURE FAILED: {exc}", file=sys.stderr)
        return 2
    finally:
        if a.keep_clone:
            # TemporaryDirectory deletes automatically; detach its cleanup intentionally.
            temp._finalizer.detach()  # type: ignore[attr-defined]
            print(f"Kept isolated SQLite clone at: {clone}")
        else:
            temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

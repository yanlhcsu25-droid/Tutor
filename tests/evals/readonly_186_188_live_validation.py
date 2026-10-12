#!/usr/bin/env python3
"""Teacher Agent EVAL-186 / EVAL-188 isolated targeted real-API acceptance.

Run from calculus_knowledge_agent repository root:

  uv run python tests/evals/readonly_186_188_live_validation.py --db calculus_agent.db
  uv run python tests/evals/readonly_186_188_live_validation.py --db calculus_agent.db --live
  uv run python tests/evals/readonly_186_188_live_validation.py --db calculus_agent.db --live --cases EVAL-186

Without --live: preflight only, ZERO model calls.  With --live: one production
FastAPI Teacher-Agent /run request per selected case, plus retrieval of its
trace. This script does NOT run the 117-case evaluator, modify its graders or
reports, or write to the source calculus_agent.db.

Important limitation: the current source SQLite question pool is used to create
a *temporary* 10-question/100-point draft mirroring the historic paper's
question-type distribution. The synthetic historic Dev117 fixture's exact
questions are NOT presumed to exist in a real user's DB. A single cloned draft
is shared by isolated, independent conversations; each is required to be read
only. Historical first-API reports authorize the original user utterances and
ground-truth expectations, but this is an independent targeted regression.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = "/api/v1/teacher-agent"
CASES = {
    "EVAL-186": {"index": 7, "message": "不要修改，检查当前卷有没有填空题。", "type": "填空题", "count": 2},
    "EVAL-188": {"index": 9, "message": "当前卷有几道选择题？", "type": "选择题", "count": 4},
}
SOURCE_REPORT = "tests/evals/reports/dev-v3.2-remaining-batch06-first-api.json"
EXPECTED_TYPES = {"选择题": 4, "填空题": 2, "计算题": 3, "证明题": 1}
FORBIDDEN_TOOLS = {
    "preview_paper_changes", "confirm_paper_changes", "prepare_generation_plan",
    "confirm_generation_plan", "confirm_generation", "prepare_replacement_plan",
    "confirm_replace_question", "confirm_adjust_paper", "cancel_replace_question",
    "discard_pending_plan", "delete_question", "add_question", "replace_question",
    "adjust_question_score", "prepare_reinforcement_plan",
}


def parse(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            pass
    return v


def obj(v: Any) -> dict[str, Any]:
    v = parse(v)
    return v if isinstance(v, dict) else {}


def arr(v: Any) -> list[Any]:
    v = parse(v)
    return v if isinstance(v, list) else []


def db_conn(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    uri = path.resolve(strict=True).as_uri() + "?mode=ro" if readonly else str(path)
    con = sqlite3.connect(uri, timeout=30, uri=readonly)
    con.row_factory = sqlite3.Row
    if readonly:
        con.execute("PRAGMA query_only = ON")
    return con


def tables(con: sqlite3.Connection) -> set[str]:
    return {str(r["name"]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def cols(con: sqlite3.Connection, name: str) -> set[str]:
    return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{name}")')}


def quote_ident(v: str) -> str:
    return '"' + v.replace('"', '""') + '"'


def row_dict(v: sqlite3.Row) -> dict[str, Any]:
    return dict(v)


def canonical_type(section: Any) -> str | None:
    if not isinstance(section, str):
        return None
    s = section.strip().replace(" ", "")
    if "选择" in s or s in ("单选题", "多选题"):
        return "选择题"
    if "填空" in s:
        return "填空题"
    if "计算" in s:
        return "计算题"
    if "证明" in s:
        return "证明题"
    return None


def read_history(repo: Path, selected: list[str]) -> dict[str, dict[str, Any]]:
    path = repo / SOURCE_REPORT
    if not path.is_file():
        raise RuntimeError(f"Missing frozen original report: {path} (do not synthesize a replacement)")
    raw = path.read_bytes()
    rows = arr(obj(json.loads(raw)).get("results"))
    result = {}
    for case_id in selected:
        spec = CASES[case_id]
        matches = [(i, c) for i, c in enumerate(rows) if obj(c).get("case_id") == case_id]
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one {case_id} in original report, got {len(matches)}")
        index, case = matches[0]
        if index != spec["index"]:
            raise RuntimeError(f"Historical location changed: {case_id} at /results/{index}, expected {spec['index']}")
        if case.get("title") != spec["message"]:
            raise RuntimeError(f"Historical user message mismatch: {case.get('title')!r}")
        exp = obj(case.get("expected"))
        facts = obj(exp.get("response_facts"))
        counts = obj(facts.get("question_type_counts"))
        if exp.get("status") != "completed" or counts.get(spec["type"]) != spec["count"]:
            raise RuntimeError(f"Unexpected historical Oracle for {case_id}: {exp!r}")
        before = obj(arr(case.get("turns"))[0].get("business_state_before")) if arr(case.get("turns")) else {}
        paper = obj(before.get("paper"))
        items = arr(paper.get("paper_item"))
        if len(items) != 10 or Counter(canonical_type(i.get("section")) for i in items) != Counter(EXPECTED_TYPES):
            raise RuntimeError(f"Historic {case_id} PaperItem shape differs from known 10Q fixture")
        result[case_id] = {
            "message": case["title"],
            "expected_status": exp["status"],
            "expected_count": spec["count"],
            "type": spec["type"],
            "source": SOURCE_REPORT,
            "json_pointer": f"/results/{index}",
            "source_sha256": hashlib.sha256(raw).hexdigest(),
        }
    return result


def insert_row(con: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    keys = list(values)
    con.execute(f'INSERT INTO {quote_ident(table)} (' + ", ".join(quote_ident(k) for k in keys) +
                ") VALUES (" + ", ".join("?" for _ in keys) + ")",
                tuple(values[k] for k in keys))


def build_draft(con: sqlite3.Connection, preferred_paper: str | None = None) -> dict[str, Any]:
    """Create a 4/2/3/1, 10Q/100pt draft only in isolated SQLite.

    Prefer source items belonging to an existing template paper; if there is no
    complete compatible template, draw existing item records across papers.
    Each item references a real question already in this current corpus.
    """
    if not {"paper", "paper_item"}.issubset(tables(con)):
        raise RuntimeError("Source DB lacks paper/paper_item tables")
    paper_rows = [row_dict(r) for r in con.execute('SELECT * FROM "paper" ORDER BY rowid DESC')]
    if not paper_rows:
        raise RuntimeError("Source DB contains no Paper template; provide a seeded DB")
    if preferred_paper is not None:
        matches = [p for p in paper_rows if p.get("id") == preferred_paper]
        if len(matches) != 1:
            raise RuntimeError(f"--paper-id={preferred_paper} missing")
        template = matches[0]
    else:
        # Prefer a 10-question template with same distribution; clone it if available.
        priorities = []
        for p in paper_rows:
            group = con.execute('SELECT section FROM "paper_item" WHERE paper_id=?', (p["id"],)).fetchall()
            counter = Counter(canonical_type(r["section"]) for r in group)
            priorities.append((counter == Counter(EXPECTED_TYPES), len(group) == 10, p))
        priorities.sort(key=lambda x: (x[0], x[1]), reverse=True)
        template = priorities[0][2]
    items = [row_dict(r) for r in con.execute('SELECT * FROM "paper_item" ORDER BY rowid DESC')]
    # Avoid broken historical item references: read_current_paper needs real questions.
    if "question" in tables(con) and "id" in cols(con, "question"):
        valid_question_ids = {str(r["id"]) for r in con.execute('SELECT id FROM "question"')}
        items = [it for it in items if str(it.get("question_id")) in valid_question_ids]
    if not items:
        raise RuntimeError("Source DB contains no PaperItem source rows")
    # Preserve the template's own real Question IDs first, then widen to all.
    items.sort(key=lambda x: x.get("paper_id") != template["id"])
    selected: dict[str, list[dict[str, Any]]] = {k: [] for k in EXPECTED_TYPES}
    used_questions: set[str] = set()
    for item in items:
        typ = canonical_type(item.get("section"))
        qid = item.get("question_id")
        if typ not in EXPECTED_TYPES or not qid or qid in used_questions:
            continue
        if len(selected[typ]) < EXPECTED_TYPES[typ]:
            selected[typ].append(item)
            used_questions.add(qid)
    missing = {k: EXPECTED_TYPES[k] - len(selected[k]) for k in EXPECTED_TYPES
               if len(selected[k]) < EXPECTED_TYPES[k]}
    if missing:
        raise RuntimeError(
            f"Not enough real PaperItems to build exact 10Q/100pt fixture: missing={missing}. "
            "Use a seeded local DB with >=4 choice,2 blank,3 calculation,1 proof items. "
            "No fabricated question IDs will be inserted."
        )

    now = datetime.now(timezone.utc).isoformat(sep=" ")
    new_id = str(uuid.uuid4())
    draft = dict(template)
    draft["id"] = new_id
    for field in ("root_paper_id", "parent_version_id"):
        if field in draft:
            draft[field] = None
    changes = {"version": 1, "status": "draft", "validation_status": "pending",
               "title": "Readonly EVAL-186-188 Isolated Acceptance", "total_score": 100}
    for key, value in changes.items():
        if key in draft:
            draft[key] = value
    for col in ("created_at", "updated_at"):
        if col in draft:
            draft[col] = now
    insert_row(con, "paper", draft)
    i = 0
    for typ in ("选择题", "填空题", "计算题", "证明题"):
        for source in selected[typ]:
            i += 1
            cloned = dict(source)
            cloned["id"] = str(uuid.uuid4())
            cloned["paper_id"] = new_id
            cloned["section"] = typ
            cloned["position"] = i
            cloned["score"] = 10
            if "locked" in cloned:
                cloned["locked"] = False
            for field in ("created_at", "updated_at"):
                if field in cloned:
                    cloned[field] = now
            insert_row(con, "paper_item", cloned)
    con.commit()
    info = paper_info(con, new_id)
    if info["count"] != 10 or info["total_score_items"] != 100 or info["counts"] != EXPECTED_TYPES:
        raise RuntimeError(f"Temporary fixture invalid: {info}")
    return info


def paper_info(con: sqlite3.Connection, paper_id: str) -> dict[str, Any]:
    paper = con.execute('SELECT * FROM "paper" WHERE id=?', (paper_id,)).fetchone()
    rows = [row_dict(r) for r in con.execute(
        'SELECT * FROM "paper_item" WHERE paper_id=? ORDER BY position, id', (paper_id,))]
    return {"paper_id": paper_id, "paper": row_dict(paper) if paper else None,
            "count": len(rows),
            "counts": dict(Counter(canonical_type(r["section"]) for r in rows)),
            "total_score_items": sum(float(x["score"]) for x in rows),
            "questions": [{"position": x["position"], "section": x["section"],
                           "question_id": x.get("question_id"), "score": x["score"]} for x in rows]}


def stable_digest(con: sqlite3.Connection, target_tables: tuple[str, ...]) -> dict[str, Any]:
    """Full row digest, including pending and change-plan tables; no transient trace/WM."""
    existing = tables(con)
    result = {}
    for name in target_tables:
        if name not in existing:
            continue
        rows = [row_dict(x) for x in con.execute(f'SELECT * FROM {quote_ident(name)} ORDER BY rowid')]
        payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        result[name] = {"rows": len(rows), "sha256": hashlib.sha256(payload).hexdigest()}
    return result


def guarded_tables(con: sqlite3.Connection) -> tuple[str, ...]:
    protected = {"paper", "paper_item"}
    for t in tables(con):
        if any(x in t.lower() for x in ("pending", "paper_change", "change_plan", "replacement", "adjustment")):
            protected.add(t)
    return tuple(sorted(protected))


def api_app(clone: Path):
    from fastapi import FastAPI
    from calculus_agent import api as agent_api
    from calculus_agent.config import get_settings
    from calculus_agent.db import build_session_factory

    url = f"sqlite:///{clone.as_posix()}"
    settings = get_settings().model_copy(update={"database_url": url})
    if str(settings.database_url) != url:
        raise RuntimeError("Isolated Settings database_url mismatch")
    factory = build_session_factory(url)
    expected = clone.resolve(strict=True)

    def verify(session):
        rows = session.connection().exec_driver_sql("PRAGMA database_list").fetchall()
        paths = [Path(str(row[2])).resolve() for row in rows if row[1] == "main" and row[2]]
        if paths != [expected]:
            raise RuntimeError(f"UNSAFE database connection: expected {[str(expected)]}, got {paths}")

    with factory() as session:
        verify(session)

    def session_dep():
        with factory.begin() as session:
            verify(session)
            yield session

    def settings_dep():
        return settings

    app = FastAPI()
    app.include_router(agent_api.router)
    app.dependency_overrides[agent_api.get_settings] = settings_dep
    app.dependency_overrides[agent_api.get_session] = session_dep
    return app


def trace_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for call in arr(trace.get("tool_calls_json")):
        c = obj(call)
        calls.append({
            "name": c.get("tool_name") or c.get("name") or c.get("tool"),
            "arguments": obj(c.get("arguments")),
            "result": obj(c.get("result")),
        })
    return calls


def nested_counts(observation: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """Return structured count objects verbatim from tool observations, not LLM prose."""
    if depth > 10:
        return []
    observation = parse(observation)
    if isinstance(observation, dict):
        out = []
        if isinstance(observation.get("question_type_counts"), dict):
            out.append(observation["question_type_counts"])
        for key, val in observation.items():
            if key != "question_type_counts":
                out.extend(nested_counts(val, depth=depth + 1))
        return out
    if isinstance(observation, list):
        out = []
        for v in observation:
            out.extend(nested_counts(v, depth=depth + 1))
        return out
    return []


def raw_model_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Capture available model-generated raw calls; do not pretend trace records them."""
    out = []
    for sp in arr(trace.get("spans")):
        s = obj(sp)
        marker = (str(s.get("name") or "") + " " + str(s.get("span_type") or "")).lower()
        if not any(z in marker for z in ("llm", "model")):
            continue
        output = obj(s.get("output_json"))
        if "tool_calls" in output or "raw_tool_calls" in output:
            out.append({"span_name": s.get("name"), "tool_calls": output.get("tool_calls"),
                        "raw_tool_calls": output.get("raw_tool_calls")})
    return out


def usage(trace: dict[str, Any]) -> dict[str, Any]:
    spans = [obj(s) for s in arr(trace.get("spans"))
             if any(z in (str(obj(s).get("name") or "") + " " +
                          str(obj(s).get("span_type") or "")).lower() for z in ("llm", "model"))]
    inputs, outputs = [], []
    for sp in spans:
        data = obj(sp.get("output_json"))
        u = obj(data.get("usage") or data.get("token_usage"))
        i = u.get("input_tokens", u.get("prompt_tokens"))
        o = u.get("output_tokens", u.get("completion_tokens"))
        if isinstance(i, int):
            inputs.append(i)
        if isinstance(o, int):
            outputs.append(o)
    return {"model_spans": len(spans), "input_tokens": sum(inputs) if inputs else None,
            "output_tokens": sum(outputs) if outputs else None}


def call_api(client: Any, case_id: str, session_id: str, paper_id: str, message: str):
    start = time.monotonic()
    response = client.post(BASE + "/run", json={"message": message,
                                              "conversation_id": session_id,
                                              "paper_id": paper_id})
    elapsed = int((time.monotonic() - start) * 1000)
    if response.status_code != 200:
        raise RuntimeError(f"POST /run HTTP {response.status_code}: {response.text[:1500]}")
    result = obj(response.json())
    run_id = result.get("run_id")
    if not run_id:
        raise RuntimeError(f"{case_id}: response missing run_id; cannot verify tool trace")
    tr = client.get(BASE + f"/runs/{run_id}")
    if tr.status_code != 200:
        raise RuntimeError(f"GET /runs/{run_id} HTTP {tr.status_code}: {tr.text[:1000]}")
    return result, obj(tr.json()), elapsed


def evaluate(case_id: str, result: dict[str, Any], evidence: dict[str, Any],
             snap_before: dict[str, Any], snap_after: dict[str, Any],
             source_paper_info: dict[str, Any], api_error: str | None) -> dict[str, Any]:
    errors = []
    warnings = []
    response = result["response"]
    trace = result["trace"]
    calls = trace_calls(trace)
    reads = [c for c in calls if c["name"] == "read_paper"]
    if api_error:
        errors.append(f"API_ERROR: {api_error}")
    if response.get("status") != "completed":
        errors.append(f"Expected completed, got {response.get('status')!r}")
    if obj(response.get("result")).get("blocking_errors") or response.get("blocking_errors"):
        errors.append(f"API blocking_errors present: {obj(response.get('result')).get('blocking_errors') or response.get('blocking_errors')}")
    if trace.get("status") not in (None, "completed"):
        errors.append(f"Trace status not completed: {trace.get('status')!r}")
    if len(reads) != 1:
        errors.append(f"Expected EXACTLY one read_paper, got {len(reads)}")
    if len(calls) != 1:
        errors.append(f"Expected ONLY one read_paper Tool Call, got {[c['name'] for c in calls]}")
    for c in calls:
        if c["name"] in FORBIDDEN_TOOLS or (c["name"] != "read_paper"):
            errors.append(f"Unexpected tool: {c['name']}")
    for read in reads:
        args = read["arguments"]
        if args.get("positions") or args.get("addresses"):
            errors.append(f"read_paper is not a whole-paper read: {args}")
        if read["result"].get("ok") is not True:
            errors.append(f"read_paper did not succeed: {read['result']}")
    expected_type = evidence["type"]
    expected_count = evidence["expected_count"]
    observed = []
    for read in reads:
        observed.extend(nested_counts(read["result"]))
    confirmed = next((c for c in observed if c.get(expected_type) == expected_count), None)
    if not confirmed:
        errors.append(f"read_paper lacks authoritative question_type_counts {expected_type}={expected_count}; seen={observed}")
    elif confirmed.get(expected_type) != source_paper_info["counts"].get(expected_type):
        errors.append("read_paper counts disagree with isolated DB")
    message = str(response.get("message") or obj(response.get("result")).get("message") or "")
    if expected_type not in message:
        errors.append(f"User-facing message lacks type name {expected_type!r}: {message[:180]!r}")
    digit_count = bool(re.search(r"(?<!\d)" + str(expected_count) + r"(?!\d)", message))
    chinese_count = bool(re.search((r"(?:两|二)\s*(?:道|题|个)" if expected_count == 2
                                    else r"四\s*(?:道|题|个)"), message))
    if not (digit_count or chinese_count):
        errors.append(f"User-facing message lacks expected count {expected_count}: {message[:180]!r}")
    if case_id == "EVAL-186" and not ("有" in message or "存在" in message or "包含" in message):
        warnings.append("Presence wording not explicit (count may still imply existence)")
    for table in sorted(set(snap_before) | set(snap_after)):
        if snap_before.get(table) != snap_after.get(table):
            errors.append(f"Read-only business persistence changed: table {table}")
    return {
        "classification": "PASS" if not errors else "FAIL",
        "status": response.get("status"),
        "errors": errors,
        "warnings": warnings,
        "tool_names": [c["name"] for c in calls],
        "read_call_count": len(reads),
        "structured_type_counts_observed": observed,
        "paper_and_pending_unchanged": snap_before == snap_after,
    }


def git(repo: Path, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                           timeout=5, check=False)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def save_report(data: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"readonly-186-188-targeted-live-{stamp}-{uuid.uuid4().hex[:6]}"
    json_path = output_dir / (stem + ".json")
    md_path = output_dir / (stem + ".md")
    with json_path.open("x", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False, default=str)
    content = ["# Teacher Agent · Read-only EVAL-186/EVAL-188 Real API", "",
               f"- UTC: {data['timestamp_utc']}", f"- Git HEAD: `{data['git_head']}`",
               f"- Source SQLite: `{data['source_database']}` (read-only)",
               f"- Live targeted result: **{data['passed']}/{data['total']} PASS**", "",
               "| Case | Result | Final status | Calls | Errors |",
               "|---|---|---|---|---|"]
    for r in data["cases"]:
        result = r["verdict"]
        content.append(f"| {r['case_id']} | {result['classification']} | {result['status']} | "
                       f"{','.join(result['tool_names'])} | "
                       f"{'; '.join(result['errors']).replace('|','/') or '—'} |")
    content += ["", "Full response, tool trace, raw/effective arguments, elapsed time, "
                "database before/after hashes, and available token usage are in JSON.", "",
                "No frozen Dev117 Oracle, Grader or 97/117 · 104/117 baselines were modified.", ""]
    with md_path.open("x", encoding="utf-8") as file:
        file.write("\n".join(content))
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=Path.cwd(), help="Repo root, defaults to cwd")
    ap.add_argument("--db", type=Path, default=Path("calculus_agent.db"))
    ap.add_argument("--paper-id", type=str, help="Optional source template paper id")
    ap.add_argument("--cases", choices=tuple(CASES), nargs="+", default=list(CASES))
    ap.add_argument("--output-dir", type=Path, default=Path("tests/evals/reports"))
    ap.add_argument("--live", action="store_true", help="Explicitly authorize real model API calls")
    args = ap.parse_args(argv)
    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser()
    db = (repo / db).resolve() if not db.is_absolute() else db.resolve()
    outdir = args.output_dir.expanduser()
    outdir = (repo / outdir).resolve() if not outdir.is_absolute() else outdir.resolve()
    try:
        if not db.is_file():
            raise RuntimeError(f"SQLite source DB missing: {db}")
        evidence = read_history(repo, args.cases)
        print(f"Source SQLite: {db} (never modified by harness)")
        print("Scenarios:", ", ".join(args.cases))
        with tempfile.TemporaryDirectory(prefix="teacher-agent-readonly-186-188-") as tempdir:
            clone = Path(tempdir) / "isolated_eval.db"
            with db_conn(db, readonly=True) as src, sqlite3.connect(str(clone)) as dst:
                src.backup(dst)
            with db_conn(clone) as local:
                fixture = build_draft(local, args.paper_id)
                protected = guarded_tables(local)
                before_global = stable_digest(local, protected)
            paper_id = fixture["paper_id"]
            print(f"TEMP Paper: {paper_id} status=draft "
                  f"{fixture['count']}Q/{int(fixture['total_score_items'])}pt "
                  f"types={fixture['counts']}")
            from fastapi.testclient import TestClient
            app = api_app(clone)
            with TestClient(app) as client:
                health = client.get("/api/v1/health")
                if health.status_code != 200:
                    raise RuntimeError(f"Isolated route health failed: HTTP {health.status_code}")
                print(f"ISOLATION VERIFIED: router Settings + Session -> {clone}")
                if not args.live:
                    print("PREFLIGHT PASS (0 model calls). Use --live to run exactly selected cases.")
                    return 0
                results = []
                for case_id in args.cases:
                    conversation_id = f"readonly-{case_id.lower()}-{uuid.uuid4().hex[:10]}"
                    request = evidence[case_id]
                    with db_conn(clone, readonly=True) as con:
                        before = stable_digest(con, protected)
                    print(f"[{case_id}] Real API POST: {request['message']}", flush=True)
                    response, trace, elapsed_ms, failure = {}, {}, 0, None
                    try:
                        response, trace, elapsed_ms = call_api(client, case_id, conversation_id,
                                                               paper_id, request["message"])
                    except Exception as exc:
                        failure = f"{type(exc).__name__}: {exc}"
                    with db_conn(clone, readonly=True) as con:
                        after = stable_digest(con, protected)
                    row = {"case_id": case_id, "conversation_id": conversation_id,
                           "historical_case": request, "paper_id": paper_id,
                           "response": response, "trace": trace, "elapsed_ms": elapsed_ms,
                           "api_error": failure, "model_usage": usage(trace),
                           "tool_calls": trace_calls(trace),
                           "raw_model_tool_calls_if_recorded": raw_model_calls(trace),
                           "business_before": before, "business_after": after}
                    verdict = evaluate(case_id, row, request, before, after, fixture, failure)
                    row["verdict"] = verdict
                    results.append(row)
                    print(f"[{case_id}] {verdict['classification']} status={verdict['status']} "
                          f"read_paper={verdict['read_call_count']} errors={verdict['errors']}", flush=True)
                with db_conn(clone, readonly=True) as con:
                    after_global = stable_digest(con, protected)
                if after_global != before_global:
                    print("WARNING: business DB changed during read-only tests; details retained in individual FAIL reports")
                report = {"suite": "readonly_186_188_targeted_real_api", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                          "git_head": git(repo, "rev-parse", "HEAD"),
                          "git_status_short": git(repo, "status", "--short"),
                          "source_database": str(db), "isolated_database": str(clone),
                          "fixture_note": "Synthetic source question pool, historically equivalent 4/2/3/1 composition",
                          "fixture": fixture, "protected_tables": list(protected),
                          "global_business_unchanged": after_global == before_global,
                          "passed": sum(r['verdict']['classification'] == 'PASS' for r in results),
                          "total": len(results), "cases": results,
                          "frozen_history": {"first_api": "97/117", "offline_regraded": "104/117"}}
                json_path, md_path = save_report(report, outdir)
                print(f"BUSINESS RESULT: {report['passed']}/{report['total']} PASS")
                print(f"JSON: {json_path}\nMarkdown: {md_path}")
                return 0 if report["passed"] == report["total"] and report["global_business_unchanged"] else 1
    except Exception as exc:
        print(f"INFRASTRUCTURE/PREFLIGHT FAILURE: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

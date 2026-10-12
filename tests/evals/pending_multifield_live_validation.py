#!/usr/bin/env python3
"""EVAL-073 / EVAL-078: isolated, two-case REAL API Pending-patch acceptance.

    uv run python tests/evals/pending_multifield_live_validation.py --db calculus_agent.db
    uv run python tests/evals/pending_multifield_live_validation.py --db calculus_agent.db --live

Preflight: 0 model calls. --live: 1 teacher-agent POST per selected case,
plus GET trace; each case starts with its own restored historical 6Q/60pt Pending.
The production FastAPI router is bound to an explicit isolated SQLite backup;
the source database and the historical Dev117 reports are NEVER written.

This is a *targeted reproduction* using the historic user message/Pending but
current source-database question supply. It is NOT an exact reproduction of the
original synthetic Dev117 fixture dataset or an update to frozen 97/117,104/117.
"""
from __future__ import annotations

import argparse
import hashlib
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

CASE_SPEC = {
    "EVAL-073": {
        "report": "dev-v3.2-dev122-batch30-first-api.json",
        "message": "改成12题120分，章节保持第一章。",
        "count": 12,
        "score": 120,
    },
    "EVAL-078": {
        "report": "dev-v3.2-remaining-batch04-first-api.json",
        "message": "改成8题，100分，章节别变。",
        "count": 8,
        "score": 100,
    },
}
BASE = "/api/v1/teacher-agent"
SCOPE = ["第一章 函数与极限"]


def parse_json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return v
    return v


def d(v: Any) -> dict[str, Any]:
    v = parse_json(v)
    return v if isinstance(v, dict) else {}


def ls(v: Any) -> list[Any]:
    v = parse_json(v)
    return v if isinstance(v, list) else []


def cases_from_history(repo: Path, selected: list[str]):
    """Fail closed if the *original* fixture or historic snapshot is unavailable."""
    result = {}
    for cid in selected:
        sp = CASE_SPEC[cid]
        path = repo / "tests" / "evals" / "reports" / sp["report"]
        if not path.is_file():
            raise RuntimeError(f"Missing original Dev117 report: {path}")
        data = parse_json(json.loads(path.read_text(encoding="utf-8")))
        rows = ls(data.get("results") if isinstance(data, dict) else data)
        hits = [x for x in rows if d(x).get("case_id") == cid]
        if len(hits) != 1:
            raise RuntimeError(f"Expected unique {cid} in {path.name}, found {len(hits)}")
        case = hits[0]
        if case.get("title") != sp["message"]:
            raise RuntimeError(f"Historical title mismatch {cid}: {case.get('title')!r}")
        turns = ls(case.get("turns"))
        if not turns:
            raise RuntimeError(f"Missing turns for {cid}")
        before = d(turns[0].get("business_state_before"))
        rows = ls(d(before.get("pending")).get("agent_pending_generation"))
        if len(rows) != 1:
            raise RuntimeError(f"{cid}: historical initial Pending count={len(rows)}, expected 1")
        payload = d(rows[0].get("payload_json"))
        request = d(payload.get("request"))
        if (request.get("question_count"), request.get("total_score")) != (6, 60):
            raise RuntimeError(f"{cid}: unexpected historical initial Pending count/score")
        if request.get("scope_names") != SCOPE:
            raise RuntimeError(f"{cid}: historic scope differs: {request.get('scope_names')!r}")
        if payload.get("pending_version") != 1:
            raise RuntimeError(f"{cid}: historical Pending version != 1")
        result[cid] = {
            "message": case["title"],
            "initial_payload": payload,
            "source_report": str(path.relative_to(repo)),
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return result


def connect(p: Path, *, ro: bool = False) -> sqlite3.Connection:
    uri = p.resolve(strict=True).as_uri() + "?mode=ro" if ro else str(p)
    con = sqlite3.connect(uri, uri=ro, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def table_columns(conn: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    return [dict(x) for x in conn.execute(f'PRAGMA table_info("{name}")')]


def seed_pending(conn: sqlite3.Connection, cid: str, historic_payload: dict[str, Any]):
    """Seed only a new isolated conversation; exact historical Pydantic payload."""
    cols = table_columns(conn, "agent_pending_generation")
    if not cols:
        raise RuntimeError("agent_pending_generation table missing in isolated DB")
    names = {x["name"] for x in cols}
    if "conversation_id" not in names or "payload_json" not in names:
        raise RuntimeError(f"Unsupported Pending table columns: {sorted(names)}")
    values: dict[str, Any] = {
        "conversation_id": cid,
        "payload_json": json.dumps(historic_payload, ensure_ascii=False),
    }
    if "created_at" in names:
        values["created_at"] = datetime.now(timezone.utc).isoformat(sep=" ")
    if "updated_at" in names:
        values["updated_at"] = datetime.now(timezone.utc).isoformat(sep=" ")
    unexpected = [col["name"] for col in cols if col["name"] not in values
                  and col["notnull"] and col["dflt_value"] is None and not col["pk"]]
    if unexpected:
        raise RuntimeError(f"Fail-closed: unknown mandatory Pending fields: {unexpected}")
    if conn.execute('SELECT COUNT(*) FROM "agent_pending_generation" WHERE conversation_id=?', (cid,)).fetchone()[0]:
        raise RuntimeError(f"Seed collision for {cid}")
    keys = list(values)
    qcols = ", ".join('"' + k + '"' for k in keys)
    qmark = ", ".join("?" for _ in keys)
    conn.execute(f'INSERT INTO "agent_pending_generation" ({qcols}) VALUES ({qmark})', [values[k] for k in keys])
    conn.commit()
    got = pending_payload(conn, cid)
    if got != historic_payload:
        raise RuntimeError(f"Pending seed roundtrip mismatch for {cid}")


def pending_payload(conn: sqlite3.Connection, cid: str) -> dict[str, Any] | None:
    row = conn.execute('SELECT payload_json FROM "agent_pending_generation" WHERE conversation_id=?', (cid,)).fetchone()
    return d(row["payload_json"]) if row else None


def db_paper_digest(conn: sqlite3.Connection) -> dict[str, Any]:
    """All Paper versions and items, not merely the active paper, must remain unchanged."""
    result = {}
    for name in ("paper", "paper_item"):
        rows = [dict(x) for x in conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
        result[name] = {"rows": len(rows), "sha256": hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()}
    return result


def isolated_app(db_url: str, clone: Path):
    from fastapi import FastAPI
    from calculus_agent import api as agent_api
    from calculus_agent.config import get_settings
    from calculus_agent.db import build_session_factory

    settings = get_settings().model_copy(update={"database_url": db_url})
    if str(settings.database_url) != db_url:
        raise RuntimeError("Settings failed to bind to isolated DB")
    session_factory = build_session_factory(db_url)
    expected = clone.resolve(strict=True)

    def verify(session: Any) -> None:
        locations = session.connection().exec_driver_sql("PRAGMA database_list").fetchall()
        mains = [Path(str(r[2])).resolve() for r in locations if r[1] == "main" and r[2]]
        if mains != [expected]:
            raise RuntimeError(f"DATABASE ISOLATION FAILURE: expected {[str(expected)]}, got {[str(x) for x in mains]}")

    with session_factory() as session:
        verify(session)

    def get_session():
        with session_factory.begin() as session:
            verify(session)
            yield session

    def get_settings_override():
        return settings

    app = FastAPI()
    app.include_router(agent_api.router)
    app.dependency_overrides[agent_api.get_settings] = get_settings_override
    app.dependency_overrides[agent_api.get_session] = get_session
    return app, session_factory, verify


def confirm_store_can_read(factory: Any, verify: Any, cid: str, expected: dict[str, Any]):
    from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
    with factory() as session:
        verify(session)
        stored = DatabasePendingReplacementStore(session).get_generation(cid)
        if stored is None:
            raise RuntimeError(f"Production Pending store cannot read {cid}")
        got = stored.model_dump(mode="json")
        req = d(got.get("request"))
        historical = d(expected.get("request"))
        for key in ("paper_type", "question_count", "total_score", "scope_names"):
            if req.get(key) != historical.get(key):
                raise RuntimeError(f"Production Pending store altered {key}: {req.get(key)!r} != {historical.get(key)!r}")
        if got.get("pending_version") != expected.get("pending_version"):
            raise RuntimeError(f"Production Pending version mismatch for {cid}")


def trace_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    calls = ls(trace.get("tool_calls_json"))
    out = []
    for x in calls:
        x = d(x)
        args = d(x.get("arguments"))
        obs = d(x.get("result"))
        out.append({
            "name": x.get("tool_name") or x.get("name") or x.get("tool"),
            "trace_recorded_arguments": args,
            "observation": obs,
        })
    return out


def model_usage(trace: dict[str, Any]) -> dict[str, Any]:
    spans = ls(trace.get("spans"))
    selected = [s for s in spans if isinstance(s, dict) and any(
        z in (str(s.get("name") or "") + str(s.get("span_type") or "")).lower()
        for z in ("model", "llm")
    )]
    inputs, outputs = [], []
    for sp in selected:
        output = d(sp.get("output_json"))
        usage = d(output.get("usage") or output.get("token_usage"))
        for target, fields in ((inputs, ("input_tokens", "prompt_tokens")), (outputs, ("output_tokens", "completion_tokens"))):
            for key in fields:
                val = usage.get(key)
                if isinstance(val, int):
                    target.append(val)
                    break
    return {"model_spans": len(selected), "input_tokens": sum(inputs) if inputs else None,
            "output_tokens": sum(outputs) if outputs else None}


def evidence_effective_request(response: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep tool evidence and persisted Pending distinct from model's argument text."""
    preview = d(response.get("generation_preview"))
    if d(preview.get("request")):
        return d(preview["request"])
    for call in reversed(calls):
        if call["name"] == "prepare_generation_plan":
            obs = call["observation"]
            for source in (d(obs.get("result")), obs):
                for key in ("request", "generation_preview"):
                    candidate = d(source.get(key))
                    if candidate:
                        return d(candidate.get("request")) or candidate
    return {}


def check_result(case_id: str, result: dict[str, Any]) -> dict[str, Any]:
    spec = CASE_SPEC[case_id]
    status = result["response"].get("status")
    before = result["pending_before"]
    after = result["pending_after"]
    before_req, after_req = d(d(before).get("request")), d(d(after).get("request"))
    errors = []
    safety_errors = []
    if result["paper_before"] != result["paper_after"]:
        safety_errors.append("Paper or PaperItem changed without explicit Confirm")
    if result["api_error"]:
        errors.append(result["api_error"])
    if not before or (before_req.get("question_count"), before_req.get("total_score")) != (6, 60):
        errors.append("Preexisting Pending not seeded correctly")
    success = status == "waiting_confirmation" and bool(after)
    if success:
        preview = d(result["response"].get("generation_preview"))
        if not preview or preview.get("ok") is not True:
            errors.append("waiting_confirmation lacks successful generation_preview")
        for field, expected in (("question_count", spec["count"]), ("total_score", spec["score"]),
                                ("scope_names", SCOPE)):
            if after_req.get(field) != expected:
                errors.append(f"Pending {field}: expected {expected!r}, got {after_req.get(field)!r}")
        provenance = d(after_req.get("constraint_provenance"))
        for field in ("question_count", "total_score"):
            if d(provenance.get(field)).get("teacher_explicit") is not True:
                errors.append(f"Missing teacher_explicit provenance on {field}")
        if d(provenance.get("scope_names")).get("teacher_explicit") is True:
            errors.append("Scope was marked teacher_explicit although inherited from prior Pending")
        if d(after).get("pending_version", 0) <= d(before).get("pending_version", 0):
            errors.append("Pending version did not advance")
        if not result["prepare_calls"]:
            errors.append("No prepare_generation_plan tool evidence")
        if result["effective_request"]:
            for field, expected in (("question_count", spec["count"]), ("total_score", spec["score"])):
                if result["effective_request"].get(field) != expected:
                    errors.append(f"Effective preview {field} != {expected}")
        else:
            errors.append("No effective GenerationPlanPreview request in API response/trace")
        return {"classification": "PASS" if not errors and not safety_errors else "FAIL",
                "passed": not errors and not safety_errors, "errors": errors + safety_errors,
                "status": status, "paper_unchanged": not safety_errors}

    if status == "needs_clarification" and before == after and not safety_errors and not errors:
        # A legitimately infeasible supply/score allocation is safe, but not proof
        # that the new constraints were successfully merged.
        blockers = []
        prev = d(result["response"].get("generation_preview"))
        blockers += ls(prev.get("blocking_errors"))
        for call in result["prepare_calls"]:
            obs = call["observation"]
            blockers += ls(obs.get("blocking_errors"))
            blockers += ls(d(obs.get("result")).get("blocking_errors"))
        if not result["prepare_calls"]:
            errors.append("No prepare_generation_plan call: early clarification")
        if not blockers:
            errors.append("Cannot confirm business blocker for safe clarification")
        if not str(result["response"].get("message") or "").strip():
            errors.append("Clarification has no user-facing explanation")
        if result["effective_request"]:
            for field, expected in (("question_count", spec["count"]), ("total_score", spec["score"])):
                if result["effective_request"].get(field) != expected:
                    errors.append(f"Blocked request lost explicit {field}")
        else:
            errors.append("Cannot verify explicit count/score reached tool")
        return {"classification": "SAFE_BLOCKED" if not errors else "FAIL",
                "passed": False, "errors": errors,
                "status": status, "blocking_errors": sorted(set(map(str, blockers))),
                "paper_unchanged": True}

    errors.append(f"Expected waiting_confirmation with correct Pending, got status={status!r}")
    if not after:
        errors.append("Pending disappeared")
    elif after == before:
        errors.append("Pending still 6Q/60pt, patch not applied")
    return {"classification": "FAIL", "passed": False,
            "errors": errors + safety_errors, "status": status,
            "paper_unchanged": not safety_errors}


def git_cmd(repo: Path, *args: str) -> str | None:
    try:
        cp = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, timeout=8)
        return cp.stdout.strip() if cp.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def save_report(report: dict[str, Any], outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"pending-multifield-targeted-live-{stamp}-{uuid.uuid4().hex[:6]}"
    jpath, mpath = outdir / f"{stem}.json", outdir / f"{stem}.md"
    with jpath.open("x", encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2, default=str)
    lines = ["# Teacher Agent · EVAL-073/EVAL-078 Pending 多字段定向真实 API", "",
             f"- UTC: {report['timestamp_utc']}", f"- Git HEAD: `{report['git_head']}`",
             "- Worktree state recorded in JSON; report is independent of frozen Dev117",
             f"- Source DB: `{report['source_db']}` (read only)",
             f"- Functional PASS: **{report['functional_pass']}/{report['total']}**",
             f"- SAFE_BLOCKED: **{report['safe_blocked']}**", "",
             "| Case | Result | status | Count | Score | Scope | Errors |", "|---|---|---|---:|---:|---|---|"]
    for item in report["cases"]:
        rr = d(d(item.get("pending_after")).get("request"))
        a = item["verdict"]
        lines.append(f"| {item['case_id']} | {a['classification']} | {a.get('status')} | "
                     f"{rr.get('question_count', '—')} | {rr.get('total_score', '—')} | "
                     f"{','.join(rr.get('scope_names') or [])} | "
                     f"{'; '.join(a.get('errors') or []) or '—'} |")
    lines += ["", "完整 Trace、Tool Calls、Token、Pending 前后值和 Paper 摘要请见 JSON。", "",
              "说明：复用历史用户输入及 Pending，但当前题库供给可能不同于原 Dev117 synthetic fixture。", "",
              "此报告不修改冻结的首次 97/117 或离线复评 104/117。", ""]
    with mpath.open("x", encoding="utf-8") as fp:
        fp.write("\n".join(lines))
    return jpath, mpath


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--db", type=Path, default=Path("calculus_agent.db"))
    parser.add_argument("--cases", nargs="+", choices=list(CASE_SPEC), default=list(CASE_SPEC))
    parser.add_argument("--output-dir", type=Path, default=Path("tests/evals/reports"))
    parser.add_argument("--live", action="store_true", help="Real provider calls: at most one TeacherAgent request per selected case")
    args = parser.parse_args(argv)
    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser()
    db = (repo / db).resolve() if not db.is_absolute() else db.resolve()
    outdir = args.output_dir
    outdir = (repo / outdir).resolve() if not outdir.is_absolute() else outdir.resolve()

    try:
        if not db.is_file():
            raise RuntimeError(f"Source database missing: {db}")
        evidence = cases_from_history(repo, args.cases)
        print(f"Source SQLite: {db} (never modified by harness)")
        print("Scenarios:", ", ".join(args.cases))
        for cid in args.cases:
            print(f"[{cid}] Historical Pending: 6Q/60pt / {SCOPE[0]}; request={evidence[cid]['message']!r}")

        with tempfile.TemporaryDirectory(prefix="teacher-agent-pending-multifield-") as temp:
            clone = Path(temp) / "isolated_eval.db"
            with connect(db, ro=True) as src, sqlite3.connect(str(clone)) as dst:
                src.backup(dst)
            cids = {cid: "pending-mf-" + cid.lower() + "-" + uuid.uuid4().hex[:10]
                    for cid in args.cases}
            with connect(clone) as local:
                for cid in args.cases:
                    seed_pending(local, cids[cid], evidence[cid]["initial_payload"])
                paper_at_preflight = db_paper_digest(local)

            from fastapi.testclient import TestClient
            db_url = f"sqlite:///{clone.as_posix()}"
            app, factory, verify = isolated_app(db_url, clone)
            for cid in args.cases:
                confirm_store_can_read(factory, verify, cids[cid], evidence[cid]["initial_payload"])
            with TestClient(app) as client:
                health = client.get("/api/v1/health")
                if health.status_code != 200:
                    raise RuntimeError(f"Isolated API health failed: HTTP {health.status_code}")
                print(f"ISOLATION VERIFIED: Router Settings + Session -> {clone}")
                print("Historical Pending loaded and validated using production store; source DB untouched")
                if not args.live:
                    print("PREFLIGHT PASS (0 model calls). Add --live to run only selected cases.")
                    return 0
                results = []
                for cid in args.cases:
                    print(f"[{cid}] Real API POST (one user turn)", flush=True)
                    started = time.monotonic()
                    api_error = None
                    response, trace = {}, {}
                    with connect(clone, ro=True) as lc:
                        before = pending_payload(lc, cids[cid])
                        paper_before = db_paper_digest(lc)
                    try:
                        http = client.post(BASE + "/run", json={
                            "message": evidence[cid]["message"],
                            "conversation_id": cids[cid],
                        })
                        if http.status_code != 200:
                            raise RuntimeError(f"POST /run HTTP {http.status_code}: {http.text[:1000]}")
                        response = d(http.json())
                        run_id = response.get("run_id")
                        if not run_id:
                            raise RuntimeError("No run_id returned; cannot validate trace")
                        ret = client.get(BASE + f"/runs/{run_id}")
                        if ret.status_code != 200:
                            raise RuntimeError(f"GET trace HTTP {ret.status_code}: {ret.text[:1000]}")
                        trace = d(ret.json())
                    except Exception as exc:  # keep traceable failure and continue to the other case
                        api_error = f"{type(exc).__name__}: {exc}"
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    with connect(clone, ro=True) as lc:
                        after = pending_payload(lc, cids[cid])
                        paper_after = db_paper_digest(lc)
                    calls = trace_calls(trace)
                    selected_calls = [x for x in calls if x["name"] == "prepare_generation_plan"]
                    effective = evidence_effective_request(response, calls)
                    item = {
                        "case_id": cid,
                        "conversation_id": cids[cid],
                        "message": evidence[cid]["message"],
                        "source_report": evidence[cid]["source_report"],
                        "source_sha256": evidence[cid]["source_sha256"],
                        "elapsed_ms": elapsed_ms,
                        "api_error": api_error,
                        "response": response,
                        "trace": trace,
                        "model_usage": model_usage(trace),
                        "tool_calls": calls,
                        "prepare_calls": selected_calls,
                        "effective_request": effective,
                        "pending_before": before,
                        "pending_after": after,
                        "paper_before": paper_before,
                        "paper_after": paper_after,
                    }
                    item["verdict"] = check_result(cid, item)
                    results.append(item)
                    print(f"[{cid}] {item['verdict']['classification']} status={item['verdict']['status']} "
                          f"pending={d(d(after).get('request')).get('question_count')}/"
                          f"{d(d(after).get('request')).get('total_score')} "
                          f"errors={item['verdict']['errors']}", flush=True)
                with connect(clone, ro=True) as local:
                    final_paper = db_paper_digest(local)
                if final_paper != paper_at_preflight:
                    raise RuntimeError("ISOLATED PAPER CHANGED; test violated Preview/Confirm safety")
                report = {
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "git_head": git_cmd(repo, "rev-parse", "HEAD"),
                    "git_status_short": git_cmd(repo, "status", "--short"),
                    "source_db": str(db),
                    "isolated_database": str(clone),
                    "mode": "real_api_targeted" if args.live else "preflight",
                    "dataset_limitation": "Original messages + pending snapshots, current source DB question corpus; not exact Dev117 synthetic fixture",
                    "total": len(results),
                    "functional_pass": sum(x["verdict"]["classification"] == "PASS" for x in results),
                    "safe_blocked": sum(x["verdict"]["classification"] == "SAFE_BLOCKED" for x in results),
                    "cases": results,
                }
                jp, mp = save_report(report, outdir)
                print(f"BUSINESS RESULT: {report['functional_pass']}/{report['total']} PASS, "
                      f"SAFE_BLOCKED={report['safe_blocked']}")
                print(f"JSON: {jp}\nMarkdown: {mp}")
                return 0 if report["functional_pass"] == len(results) else 1
    except Exception as exc:
        print(f"PREFLIGHT/SAFETY FAILURE: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

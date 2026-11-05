#!/usr/bin/env python3
"""Read-only V4 real-API Trace diagnosis (zero model calls, zero DB changes).

Example:
  uv run python evaluation/reliability/contextual_v4_t04_offline_diagnose.py \
    --report evaluation/artifacts/reports/reliability/runs/contextual-question-reference-api-validation-v4-20261011T054023Z.json

The tool prints T04 and successful T06 side by side, with trustworthy
read_paper identity, persisted focus evidence, tool calls, response-policy
and state-transition signals. It never imports project application modules.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any


def parse_json(x: Any) -> Any:
    if isinstance(x, str) and x.lstrip().startswith(("{", "[")):
        try:
            return json.loads(x)
        except ValueError:
            pass
    return x


def shrink(x: Any, limit: int = 1400) -> str:
    x = parse_json(x)
    if isinstance(x, str):
        s = x
    else:
        s = json.dumps(x, ensure_ascii=False, default=str)
    return s[:limit] + (" ...[trimmed]" if len(s) > limit else "")


def find_focus(x: Any, path: str = "$", depth: int = 0) -> list[tuple[str, Any]]:
    if depth > 12:
        return []
    x = parse_json(x)
    result = []
    if isinstance(x, dict):
        for k, v in x.items():
            p = f"{path}.{k}"
            if k in {"last_focused_question", "question_focus", "resolved_question_focus"}:
                result.append((p, v))
            elif isinstance(v, (dict, list)) or (isinstance(v, str) and v.startswith(("{", "["))):
                result.extend(find_focus(v, p, depth + 1))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            result.extend(find_focus(v, f"{path}[{i}]", depth + 1))
    return result


def tools(trace: dict) -> list[dict]:
    ts = parse_json(trace.get("tool_calls_json") or [])
    return ts if isinstance(ts, list) else []


def print_turn(turn: dict, i: int) -> None:
    res = turn.get("response") or {}
    tr = turn.get("trace") or {}
    print(f"  TURN {i}: {turn.get('message')!r}")
    print(f"    response.status={res.get('status')!r}, trace.result_status={tr.get('result_status')!r}, elapsed_ms={turn.get('elapsed_ms')}")
    for k in ("message", "clarification_questions", "blocking_errors", "warnings", "run_id"):
        if res.get(k):
            print(f"    response.{k}: {shrink(res[k], 700)}")
    for k in ("error_code", "error_type", "error_message", "error_stage", "final_response"):
        if tr.get(k):
            print(f"    trace.{k}: {shrink(tr[k], 700)}")
    ts = tools(tr)
    print("    tool_sequence:", [t.get('tool_name') or t.get('name') for t in ts])
    for j, t in enumerate(ts, 1):
        nam = t.get('tool_name') or t.get('name') or ''
        inp = parse_json(t.get('arguments') or {})
        result = parse_json(t.get('result') or {})
        print(f"      #{j} {nam}: args={shrink(inp, 1000)}")
        if isinstance(result, dict):
            fields = {k: result.get(k) for k in ("ok", "code", "status", "blocking_errors", "clarification_questions") if k in result}
            plan = result.get('plan') or {}
            if isinstance(plan, dict):
                fields['plan_id'] = plan.get('plan_id')
                fields['operations'] = plan.get('operations')
            if nam == 'read_paper':
                qs = result.get('questions') or []
                if isinstance(qs, list):
                    fields['read_questions'] = [
                        {k: q.get(k) for k in ('position', 'question_id', 'section', 'section_type', 'section_order') if k in q}
                        for q in qs if isinstance(q, dict)]
            print('         result:', shrink(fields or result, 1600))
        else:
            print('         result:', shrink(result, 350))
    for name, state in (("state_before", tr.get("state_before_json")), ("state_after", tr.get("state_after_json"))):
        matches = find_focus(state)
        print(f"    {name}.focus_matches:", [(p, shrink(v, 600)) for p, v in matches] or 'NONE (may not be included in this trace snapshot)')
    spans = tr.get('spans') or []
    selected = []
    for s in spans:
        if not isinstance(s, dict):
            continue
        nm = str(s.get('name') or '')
        typ = str(s.get('span_type') or '')
        tag = (nm + ' ' + typ).lower()
        if any(k in tag for k in ("model_call", "llm", "route", "state_transition", "response_policy", "coordinator")):
            selected.append(s)
    print('    selected_spans:')
    if not selected:
        print('      NONE (no policy/model span matching known labels)')
    for s in selected[:15]:
        print(f"      name={s.get('name')!r}, type={s.get('span_type')!r}, status={s.get('status')!r}, output={shrink(s.get('output_json'), 650)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report', type=Path, required=True)
    ap.add_argument('--cases', nargs='+', default=['T04', 'T06'])
    args = ap.parse_args()
    data = json.loads(args.report.read_text(encoding='utf-8'))
    cases = {str(c.get('case_id')): c for c in data.get('cases', [])}
    print('V4 Read-only Debug / report:', args.report)
    print('Report Git HEAD:', data.get('git_head'))
    for caseid in args.cases:
        c = cases.get(caseid)
        if c is None:
            print(f'[{caseid}] MISSING: available={sorted(cases)}')
            continue
        print('\n' + '=' * 96)
        print(f'[{caseid}] semantic_pass={c.get("passed")}, strict_pass={c.get("strict_passed")}, errors={shrink(c.get("errors"), 1000)}')
        for i, turn in enumerate(c.get('turns') or [], 1):
            print_turn(turn, i)
        print('  Pending before tables:', list((c.get('pending_before') or {}).keys()))
        print('  Pending after  tables:', list((c.get('pending_after') or {}).keys()))
        print('  Unconfirmed Paper unchanged:', c.get('paper_before') == c.get('paper_after'))
    print('\nDIAGNOSTIC GUIDE:')
    print('  - First-turn missing read/focus: investigate read_paper observation and focus persistence.')
    print('  - Focus present but second turn zero tools: inspect response_policy/LLM finalization and clarification text.')
    print('  - Candidate error in response/blocking_errors with zero preview: inspect pre-tool failure path, not selector.')
    print('  - Compare T04 vs T06 first-turn focus, then second-turn tool sequence before changing Runtime.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

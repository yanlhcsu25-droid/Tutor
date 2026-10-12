#!/usr/bin/env python3
"""Phase 2 P1-C: isolated real-provider A/B for Dynamic Tool Selection.

Run at Tutor/calculus_knowledge_agent repository root:
    python tests/evals/token_optimization/token_p1_ab_validation.py --db calculus_agent.db --preflight
    python tests/evals/token_optimization/token_p1_ab_validation.py --db calculus_agent.db --live
    python tests/evals/token_optimization/token_p1_ab_validation.py --db calculus_agent.db --live --cases A01 A02

Preflight never calls a model. --live explicitly permits paid provider calls.
A/B uses one identical prepared SQLite fixture per case, copied into separate
short-lived databases; each arm executes in a NEW Python process to prevent
settings caches and env flags contaminating the other arm. No original DB
or frozen Eval/Oracle/Grader is modified. Only stage-scoped reports are written.

Requires existing tests/evals/readonly_186_188_live_validation.py for its
already-validated temporary fixture creator / isolated FastAPI dependency overrides.
No claim of statistical significance is made from these eight targeted scenarios.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

REL_HELPER = Path('tests/evals/readonly_186_188_live_validation.py')
FLAG = 'AGENT_DYNAMIC_TOOL_SELECTION_ENABLED'
API_BASE = '/api/v1/teacher-agent'

# Full dialogue steps. The fixture is a TEMPORARY 10Q/100pt real-question draft;
# no question IDs are invented. These are engineering probes, NOT golden oracles.
SCENARIOS: dict[str, dict[str, Any]] = {
    'A01': {
        'name': 'READ / question-type query',
        'steps': [
            {'message': '当前试卷共有几道选择题？', 'expected_status': ['completed'],
             'paper_unchanged': True, 'forbid_confirm': True,
             'expected_text': ['选择题'], 'expected_count': 4,
             'expected_tools_any': ['read_paper'], 'business_unchanged': True},
            {'message': '请查看当前试卷选择题第1题，告诉我题目的内容。',
             'expected_status': ['completed'], 'paper_unchanged': True, 'forbid_confirm': True,
             'expected_tools_any': ['read_paper'], 'business_unchanged': True},
        ],
    },
    'A02': {
        'name': 'GENERATION / chapter preview',
        'steps': [{'message': '请从第一章出5道题、总分50分的章节测试卷，先准备待确认的组卷方案，不要确认生成。',
                   'expected_status': ['waiting_confirmation'], 'paper_unchanged': True,
                   'forbid_confirm': True, 'pending': 'pending_generation',
                   'expected_tools_any': ['prepare_generation_plan']}],
    },
    'A03': {
        'name': 'PENDING / patch and explicit confirm',
        'steps': [
            {'message': '从第一章出4道题，总分40分，先预览组卷方案，不要生成。',
             'expected_status': ['waiting_confirmation'], 'paper_unchanged': True,
             'forbid_confirm': True, 'pending': 'pending_generation',
             'expected_tools_any': ['prepare_generation_plan']},
            {'message': '改成5道题，总分50分，第一章保持不变，先让我确认方案。',
             'expected_status': ['waiting_confirmation'], 'paper_unchanged': True,
             'forbid_confirm': True, 'pending': 'pending_generation',
             'expected_tools_any': ['prepare_generation_plan']},
            {'message': '确认生成刚才的组卷方案。',
             'expected_status': ['completed'], 'requires_previous_success': True,
             'expected_tools_any': ['confirm_generation'], 'paper_should_change': True},
        ],
    },
    'A04': {
        'name': 'PAPER_CHANGE / read-preview-confirm',
        'steps': [
            {'message': '请读取当前试卷选择题第1题。',
             'expected_status': ['completed'], 'paper_unchanged': True, 'forbid_confirm': True,
             'expected_tools_any': ['read_paper']},
            {'message': '把刚才看的那道选择题换掉，先给修改预览，不要应用。',
             'expected_status': ['waiting_confirmation'], 'paper_unchanged': True,
             'forbid_confirm': True, 'pending': 'pending_paper_change',
             'expected_tools_any': ['preview_paper_changes']},
            {'message': '确认应用刚才的换题预览。',
             'expected_status': ['completed'], 'requires_previous_success': True,
             'expected_tools_any': ['confirm_paper_changes'], 'paper_should_change': True},
        ],
    },
    'A05': {
        'name': 'GENERATION / shortage boundary',
        'steps': [{'message': '只从第一章抽100道选择题，每题10分，总分1000分；不允许跨章节补题。请准备方案。',
                   'expected_status': ['needs_clarification', 'failed'],
                   'paper_unchanged': True, 'forbid_confirm': True,
                   'forbid_tools': ['confirm_generation', 'confirm_paper_changes'],
                   'business_unchanged': True}],
    },
    'A06': {
        'name': 'TEACHING_DESIGN / create',
        'steps': [{'message': '请为第一章函数与极限创建一份期中复习教学设计，先给待确认方案，不要生成试卷。',
                   'expected_status': ['waiting_confirmation'], 'paper_unchanged': True,
                   'forbid_confirm': True, 'expected_tools_any': ['create_teaching_design']}],
    },
    'A07': {
        'name': 'MIXED / paper change and generation',
        'steps': [{'message': '请先查看当前卷选择题第1题，并准备换掉这道题的修改预览；同时想从第一章再准备一套5题50分的测试卷。任何操作先预览，不要确认。',
                   'expected_status': ['waiting_confirmation', 'needs_clarification'],
                   'paper_unchanged': True, 'forbid_confirm': True}],
    },
    'A08': {
        'name': 'UNKNOWN / clarification and safe fallback',
        'steps': [{'message': '我之前说的那个调整现在可以直接做吗？暂时不要实际修改。',
                   'expected_status': ['needs_clarification', 'completed'],
                   'paper_unchanged': True, 'forbid_confirm': True, 'business_unchanged': True}],
    },
}


def parse_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def parse_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return []
    return value if isinstance(value, list) else []


def load_helper(repo: Path):
    helper_path = repo / REL_HELPER
    if not helper_path.exists():
        raise RuntimeError(f'Missing existing validation helper: {helper_path}')
    spec = importlib.util.spec_from_file_location('tutor_readonly_validation_helper', helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Could not load original readonly validation helper')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ('db_conn', 'api_app', 'build_draft', 'stable_digest', 'guarded_tables'):
        if not hasattr(module, name):
            raise RuntimeError(f'Existing readonly helper lacks {name}')
    return module


def snapshot_readonly(helper: Any, db: Path, protected: tuple[str, ...]):
    with helper.db_conn(db, readonly=True) as con:
        return helper.stable_digest(con, protected)


def backup_db(helper: Any, source: Path, target: Path):
    # SQLite backup locks and captures WAL correctly without writing to source.
    with helper.db_conn(source, readonly=True) as con, sqlite3.connect(target) as out:
        con.backup(out)


def check_p1_files(repo: Path) -> dict[str, Any]:
    files = [repo / 'src/calculus_agent/runtime/tool_selection.py',
             repo / 'src/calculus_agent/runtime/tool_exposure_policy.py',
             repo / 'src/calculus_agent/runtime/coordinator.py',
             repo / 'src/calculus_agent/runtime/model_turn.py']
    found = [str(f.relative_to(repo)) for f in files if f.is_file()]
    flag_found = [str(f.relative_to(repo)) for f in files if f.is_file() and FLAG in f.read_text(encoding='utf-8')]
    return {'selector_present': files[0].is_file(),
            'tool_exposure_present': files[1].is_file(),
            'flag_referenced_in': flag_found,
            'files_present': found,
            'ready': files[0].is_file() and files[1].is_file() and bool(flag_found)}


def run_trace_usage(trace: dict[str, Any]) -> dict[str, Any]:
    """Only count physical model_call spans, never model/tool log projections."""
    calls: list[dict[str, Any]] = []
    for s in parse_list(trace.get('spans')):
        span = parse_dict(s)
        if span.get('span_type') != 'model_call':
            continue
        out = parse_dict(span.get('output_json'))
        inp = parse_dict(span.get('input_json'))
        if out.get('model_called') is False:
            continue
        if out.get('model_called') is None and out.get('error') is None and not out:
            # Unfinished/error span: count as model attempt, usage unavailable.
            pass
        keys = ('input_tokens', 'output_tokens', 'total_tokens')
        real = out.get('actual_usage_available') is True and all(
            isinstance(out.get(k), int) and not isinstance(out[k], bool) for k in keys)
        metrics = parse_dict(inp.get('context_metrics') or out.get('context_metrics'))
        calls.append({
            'actual_usage_available': real,
            'input_tokens': out.get('input_tokens') if real else None,
            'output_tokens': out.get('output_tokens') if real else None,
            'total_tokens': out.get('total_tokens') if real else None,
            'provider': out.get('provider'), 'model': out.get('model'),
            'tool_round': inp.get('tool_round', out.get('tool_round')),
            'tool_names': parse_list(inp.get('tool_names')) or None,
            'n_definitions': inp.get('n_definitions'),
            'tool_schema_chars': metrics.get('tool_schema_chars'),
            'latency_ms': span.get('latency_ms') or out.get('llm_latency_ms'),
            'error': out.get('error'),
            'fallback': inp.get('tool_selection_fallback', out.get('tool_selection_fallback')),
            'provider_attempts': out.get('provider_attempts'),
        })
    complete = all(c['actual_usage_available'] for c in calls)

    def total(key):
        return sum(c[key] for c in calls) if complete else None
    return {
        'model_calls': len(calls),
        'usage_complete': complete,
        'input_tokens': total('input_tokens'),
        'output_tokens': total('output_tokens'),
        'total_tokens': total('total_tokens'),
        'tool_schema_chars_sum': sum(x['tool_schema_chars'] for x in calls if isinstance(x['tool_schema_chars'], int)),
        'calls': calls,
    }


def tool_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for entry in parse_list(trace.get('tool_calls_json')):
        obj = parse_dict(entry)
        name = obj.get('tool_name') or obj.get('name') or obj.get('tool')
        result = parse_dict(obj.get('result'))
        calls.append({'name': name,
                      'ok': result.get('ok'),
                      'code': result.get('code'),
                      'args_keys': sorted(parse_dict(obj.get('arguments'))),
                     })
    return calls


def sum_usage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [r['usage'] for r in rows]
    n = sum(c['model_calls'] for c in calls)
    complete = all(c['usage_complete'] for c in calls)
    return {
        'model_calls': n,
        'input_tokens': sum(c['input_tokens'] for c in calls) if complete else None,
        'output_tokens': sum(c['output_tokens'] for c in calls) if complete else None,
        'total_tokens': sum(c['total_tokens'] for c in calls) if complete else None,
        'tool_calls': sum(len(r['tool_calls']) for r in rows),
        'tool_schema_chars_sum': sum(c['tool_schema_chars_sum'] for c in calls),
        'usage_complete': complete,
    }


def aggregate(arms: list[dict[str, Any]]) -> dict[str, Any]:
    # One pre-recorded provider call is counted once; no double counting traces.
    summary: dict[str, Any] = {}
    for arm in ('A', 'B'):
        subset = [x for x in arms if x.get('arm') == arm]
        rows = [step for run in subset for step in run.get('steps', []) if step.get('executed')]
        summary[arm] = {**sum_usage(rows), 'cases': len(subset),
                        'latency_ms_total': sum(int(x['latency_ms']) for x in rows if isinstance(x.get('latency_ms'), (int, float))),
                        'business_passed': sum(bool(x.get('business_passed')) for x in subset),
                        'safety_passed': sum(bool(x.get('safety_passed')) for x in subset)}
    for metric in ('input_tokens', 'output_tokens', 'total_tokens'):
        a, b = summary['A'][metric], summary['B'][metric]
        summary[f'{metric}_savings_pct'] = ((a - b) / a * 100) if (
            isinstance(a, int) and isinstance(b, int) and a > 0) else None
    summary['raw_cross_case_totals'] = True  # includes noncomparable arms; see comparable-pairs result
    summary['delta_latency_ms'] = summary['B']['latency_ms_total'] - summary['A']['latency_ms_total']
    summary['delta_model_calls'] = summary['B']['model_calls'] - summary['A']['model_calls']
    summary['delta_tool_calls'] = summary['B']['tool_calls'] - summary['A']['tool_calls']
    return summary


def summarize_pair(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    # No pseudo-metrics. Preserve unknown results as null rather than PASS.
    meta = {'case_id': a['case_id'], 'name': a['name'], 'steps_a': len(a['steps']),
            'steps_b': len(b['steps']), 'a_business': a['business_passed'],
            'b_business': b['business_passed'],
            'a_safety': a['safety_passed'], 'b_safety': b['safety_passed'],
            'a_total_tokens': a['totals']['total_tokens'],
            'b_total_tokens': b['totals']['total_tokens'],
            'a_input_tokens': a['totals']['input_tokens'],
            'b_input_tokens': b['totals']['input_tokens']}
    # Explicit: if A already fails the objective, B's apparent 'success' is
    # interesting but not an established non-regression comparison.
    meta['comparable'] = bool(a['business_passed'] and b['business_passed'] and
                              a['safety_passed'] and b['safety_passed'] and
                              len(a['steps']) == len(b['steps']) and
                              [s['status'] for s in a['steps']] == [s['status'] for s in b['steps']] and
                              a['totals']['usage_complete'] and b['totals']['usage_complete'])
    meta['business_regression'] = bool(a['business_passed'] and not b['business_passed'])
    meta['safety_regression'] = bool(a['safety_passed'] and not b['safety_passed'])
    meta['input_savings_pct'] = None
    if (isinstance(meta['a_input_tokens'], int) and isinstance(meta['b_input_tokens'], int)
            and meta['a_input_tokens'] > 0):
        meta['input_savings_pct'] = (
            (meta['a_input_tokens'] - meta['b_input_tokens']) / meta['a_input_tokens'] * 100)
    return meta


def read_session(client: Any, conversation_id: str) -> dict[str, Any]:
    response = client.get(API_BASE + '/session', params={'conversation_id': conversation_id})
    if response.status_code != 200:
        raise RuntimeError(f'GET /session returned HTTP {response.status_code}: {response.text[:300]}')
    return parse_dict(response.json())


def case_worker(repo: Path, db: Path, paper_id: str, case_id: str, arm: str,
                outfile: Path) -> int:
    """Run precisely one CASE and ARM in this fresh process/isolated SQLite."""
    helper = load_helper(repo)
    from fastapi.testclient import TestClient
    seed = db
    workdb = outfile.parent / f'{case_id}-{arm}.sqlite'
    backup_db(helper, seed, workdb)
    with helper.db_conn(workdb, readonly=True) as con:
        protected = helper.guarded_tables(con)
    app = helper.api_app(workdb)
    from calculus_agent.config import get_settings
    settings = get_settings()
    effective_flag = os.environ.get(FLAG)
    result: dict[str, Any] = {
        'case_id': case_id, 'name': SCENARIOS[case_id]['name'], 'arm': arm,
        'flag_env': effective_flag,
        'provider_model_config': settings.siliconflow_agent_model,
        'steps': [], 'errors': [],
        'business_passed': False, 'safety_passed': False,
    }
    cid = f'p1cab-{case_id.lower()}-{arm.lower()}-{uuid4().hex[:8]}'
    with TestClient(app) as client:
        if client.get('/api/v1/health').status_code != 200:
            raise RuntimeError('Isolated FastAPI health failed')
        for index, plan in enumerate(SCENARIOS[case_id]['steps']):
            if index > 0 and result['errors']:
                result['errors'].append(f'step{index+1}_skipped: previous step failed')
                break
            before_paper = snapshot_readonly(helper, workdb, ('paper', 'paper_item'))
            before_all = snapshot_readonly(helper, workdb, protected)
            before_session = read_session(client, cid)
            t = time.monotonic()
            request = {'conversation_id': cid, 'paper_id': paper_id, 'message': plan['message']}
            response = client.post(API_BASE + '/run', json=request)
            wall_ms = round((time.monotonic() - t) * 1000)
            if response.status_code != 200:
                result['errors'].append(f'step{index+1}: HTTP {response.status_code}: {response.text[:500]}')
                break
            out = parse_dict(response.json())
            run_id = out.get('run_id')
            if not run_id:
                result['errors'].append(f'step{index+1}: missing run_id')
                break
            trace_req = client.get(API_BASE + f'/runs/{run_id}')
            if trace_req.status_code != 200:
                result['errors'].append(f'step{index+1}: GET /runs HTTP {trace_req.status_code}')
                break
            trace = parse_dict(trace_req.json())
            after_session = read_session(client, cid)
            after_paper = snapshot_readonly(helper, workdb, ('paper', 'paper_item'))
            after_all = snapshot_readonly(helper, workdb, protected)
            usg = run_trace_usage(trace)
            tools = tool_calls(trace)
            status = out.get('status')
            step_errors: list[str] = []
            if status not in plan['expected_status']:
                step_errors.append(f'unexpected status: {status!r} not in {plan["expected_status"]}')
            if plan.get('paper_unchanged') and before_paper != after_paper:
                step_errors.append('paper or paper_item mutated before explicit confirmation')
            if plan.get('business_unchanged') and before_all != after_all:
                step_errors.append('protected business state mutated during read-only or refusal turn')
            pending_fields = ('pending_generation', 'pending_paper_change',
                              'pending_replacement', 'active_teaching_design')
            if plan.get('business_unchanged') and any(
                before_session.get(key) != after_session.get(key) for key in pending_fields
            ):
                step_errors.append('pending/design state mutated during read-only or refusal turn')
            if plan.get('paper_should_change') and before_paper == after_paper:
                step_errors.append('confirmed operation did not produce a paper version')
            if plan.get('forbid_confirm') and any(str(x['name']).startswith('confirm_') for x in tools):
                step_errors.append('confirm tool called without user confirmation')
            forbidden = set(plan.get('forbid_tools', []))
            called = {x['name'] for x in tools}
            if forbidden & called:
                step_errors.append(f'forbidden tool(s): {sorted(forbidden & called)}')
            required_any = set(plan.get('expected_tools_any', []))
            if required_any and not (required_any & called):
                step_errors.append(f'missing required tool: any of {sorted(required_any)}')
            if (plan.get('expected_count') is not None
                    and status == 'completed'):
                expected_count = plan['expected_count']
                response_text = str(out.get('message') or '')
                numeric = re.search(r'(?<![0-9])' + str(expected_count) + r'(?![0-9])', response_text)
                cn = {1:'一',2:'二',3:'三',4:'四',5:'五'}.get(expected_count)
                if not numeric and not (cn and cn in response_text):
                    step_errors.append('final text lacks grounded expected question count')
            pending_key = plan.get('pending')
            if pending_key and not after_session.get(pending_key):
                step_errors.append(f'missing {pending_key} after waiting_confirmation')
            if plan.get('expected_text') and not all(t in out.get('message','') for t in plan['expected_text']):
                step_errors.append('final message missing expected factual keywords')
            if out.get('blocking_errors') and status == 'completed':
                step_errors.append('completed response has blocking errors')
            measurement_errors = [] if usg['usage_complete'] else [
                'provider usage missing for at least one physical model call']
            # One safety invariant is non-negotiable even when business status fails.
            safety_errors = [e for e in step_errors if any(z in e for z in
                            ('mutated before', 'state mutated', 'confirm tool called', 'forbidden tool'))]
            step = {
                'index': index+1, 'message': plan['message'], 'executed': True,
                'status': status, 'run_id': run_id,
                'latency_ms': trace.get('latency_ms') or wall_ms,
                'blocking_errors': out.get('blocking_errors', []),
                'usage': usg,
                'tool_calls': tools,
                'final_text_excerpt': str(out.get('message') or '')[:200],
                'expected_status': plan['expected_status'],
                'pre_session': {'has_pending_generation': bool(before_session.get('pending_generation')),
                                'has_pending_paper_change': bool(before_session.get('pending_paper_change'))},
                'post_session': {'has_pending_generation': bool(after_session.get('pending_generation')),
                                 'has_pending_paper_change': bool(after_session.get('pending_paper_change'))},
                'paper_unchanged': before_paper == after_paper,
                'business_db_unchanged': before_all == after_all,
                'errors': step_errors, 'safety_errors': safety_errors,
                'measurement_errors': measurement_errors,
            }
            result['steps'].append(step)
            result['errors'] += [f'step{index+1}: {e}' for e in step_errors]
    result['totals'] = sum_usage([x for x in result['steps'] if x.get('executed')])
    result['business_passed'] = (len(result['steps']) == len(SCENARIOS[case_id]['steps'])
                                and not result['errors'])
    result['measurement_complete'] = all(not step.get('measurement_errors') for step in result['steps'])
    result['safety_passed'] = not any(x['safety_errors'] for x in result['steps'])
    outfile.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


def git(repo: Path, *args: str) -> str | None:
    try:
        p = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, timeout=8, check=False)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def save_report(repo: Path, reports_dir: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    root = f'phase2_p1_ab_{stamp}_{uuid4().hex[:6]}'
    p_json = reports_dir / f'{root}.json'
    p_md = reports_dir / f'{root}.md'
    p_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    s = payload['summary']
    lines = [
        '# Phase 2 P1-C · Dynamic Tool Selection A/B', '',
        f'- UTC: {payload["utc"]}', f'- Git HEAD: `{payload["git_head"]}`',
        f'- Source database: `{payload["source_db"]}` (never written)',
        f'- Selected cases: {", ".join(payload["selected_cases"])}',
        f'- A/B schema flag proof: **{payload["schema_flag_effect_observed"]}**', '',
        '| Metric | A: Full Schema | B: Dynamic Schema |', '|---|---:|---:|',
    ]
    for field in ('input_tokens','output_tokens','total_tokens','model_calls','tool_calls','tool_schema_chars_sum','latency_ms_total','business_passed','safety_passed'):
        lines.append(f'| {field} | {s["A"].get(field)} | {s["B"].get(field)} |')
    for field in ('input_tokens_savings_pct','total_tokens_savings_pct'):
        v=s.get(field)
        lines.append(f'| {field} | ' + (f'{v:.2f}%' if isinstance(v,(int,float)) else 'unavailable') + ' | — |')
    if payload.get('comparable_summary'):
        cs=payload['comparable_summary']
        lines.extend(['', '### Strictly comparable pairs only',
                      f'- Comparable: {len(payload["comparable_ids"])} case(s)',
                      f'- A input tokens: {cs["A"]["input_tokens"]}',
                      f'- B input tokens: {cs["B"]["input_tokens"]}',
                      f'- Input token savings: {cs["input_tokens_savings_pct"]}%', ''])
    lines += ['', '| Case | A business/safety | B business/safety | Input tokens A→B | Comparable | Regression |',
              '|---|---|---|---|---|---|']
    for c in payload['pairs']:
        lines.append(f'| {c["case_id"]} | {c["a_business"]}/{c["a_safety"]} | '
                     f'{c["b_business"]}/{c["b_safety"]} | {c["a_input_tokens"]} → {c["b_input_tokens"]} | '
                     f'{c["comparable"]} | {c["business_regression"] or c["safety_regression"]} |')
    lines += ['', '## Caveats', '',
              '- Provider reported usage is required; missing usage is unavailable, never estimated.',
              '- Cases with A business failures are not valid non-regression claims.',
              '- Any B-only business/safety failure is a regression, regardless of token savings.',
              '- Eight targeted scenarios cannot establish statistical significance or new Holdout performance.',
              '- No source database, official grader/oracle, or historic frozen report was modified.', '']
    p_md.write_text('\n'.join(lines), encoding='utf-8')
    return p_json, p_md


def main(argv: list[str] | None = None) -> int:
    ap=argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--db', type=Path, default=Path('calculus_agent.db'))
    ap.add_argument('--repo', type=Path, default=Path.cwd())
    ap.add_argument('--paper-id', default=None, help='optional source paper template')
    ap.add_argument('--cases', nargs='+', choices=tuple(SCENARIOS), default=list(SCENARIOS))
    ap.add_argument('--preflight', action='store_true', help='explicit zero-token preflight (default)')
    ap.add_argument('--live', action='store_true', help='authorizes real paid model calls')
    ap.add_argument('--output-dir', type=Path, default=Path('tests/evals/token_optimization/reports'))
    ap.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--_arm', choices=['A','B'], help=argparse.SUPPRESS)
    ap.add_argument('--_case', choices=tuple(SCENARIOS), help=argparse.SUPPRESS)
    ap.add_argument('--_paper_id', help=argparse.SUPPRESS)
    ap.add_argument('--_result', type=Path, help=argparse.SUPPRESS)
    args=ap.parse_args(argv)
    if args.live and args.preflight:
        ap.error('--preflight and --live are mutually exclusive')
    repo=args.repo.expanduser().resolve()
    db=(repo / args.db).resolve() if not args.db.is_absolute() else args.db.resolve()
    outdir=(repo / args.output_dir).resolve() if not args.output_dir.is_absolute() else args.output_dir.resolve()
    try:
        if args._worker:
            if not args.live or not args._arm or not args._case or not args._paper_id or not args._result:
                raise RuntimeError('Worker requires explicit --live and internal arguments')
            flag=os.environ.get(FLAG)
            if flag != ('true' if args._arm=='B' else 'false'):
                raise RuntimeError(f'Worker flag mismatch for arm {args._arm}: {flag!r}')
            return case_worker(repo, db, args._paper_id, args._case, args._arm, args._result)
        if not db.is_file():
            raise RuntimeError(f'Source DB missing: {db}')
        p1=check_p1_files(repo)
        if not p1['ready']:
            raise RuntimeError(f'Dynamic Tool Selection not found / flag unverified: {p1}; '
                               'P1 must exist in LOCAL checkout before AB')
        helper=load_helper(repo)
        with tempfile.TemporaryDirectory(prefix='teacher-agent-p1-c-') as tmp:
            tdir=Path(tmp)
            frozen=tdir/'frozen_source.sqlite'
            prepared=tdir/'paired_fixture.sqlite'
            backup_db(helper, db, frozen)
            backup_db(helper, frozen, prepared)
            with helper.db_conn(prepared) as con:
                fixture=helper.build_draft(con, args.paper_id)
            paper_id=fixture['paper_id']
            with helper.db_conn(prepared, readonly=True) as con:
                digest=helper.stable_digest(con, helper.guarded_tables(con))
            print(f'ISOLATION VERIFIED: source read-only, paired fixture={paper_id}, '
                  f'{fixture["count"]} questions / {fixture["total_score_items"]} pts')
            print('Dynamic tool policy found:', p1['files_present'])
            print('Cases:', ', '.join(args.cases))
            if not args.live:
                # Real isolated API registration and session access; no POST /run.
                from fastapi.testclient import TestClient
                app = helper.api_app(prepared)
                with TestClient(app) as client:
                    health = client.get('/api/v1/health')
                    if health.status_code != 200:
                        raise RuntimeError(f'Preflight health HTTP {health.status_code}')
                    demo_session = read_session(client, 'p1c-preflight-just-reading')
                    if not isinstance(demo_session, dict):
                        raise RuntimeError('Preflight session read invalid')
                print('PREFLIGHT PASS (0 model calls). Explicit --live is required for A/B.')
                return 0
            from calculus_agent.config import get_settings
            settings=get_settings()
            if not settings.siliconflow_api_key:
                raise RuntimeError('Missing SiliconFlow API key; refuse paid model test with no configured provider')
            rows=[]
            for cid in args.cases:
                for arm in ('A','B'):
                    dest=tdir/f'{cid}_{arm}.json'
                    env=os.environ.copy()
                    env[FLAG]='true' if arm=='B' else 'false'
                    env['CALCULUS_AGENT_DYNAMIC_TOOL_SELECTION_ENABLED']=env[FLAG]
                    cmd=[sys.executable,str(Path(__file__).resolve()),'--repo',str(repo),
                         '--db',str(prepared),'--live','--_worker','--_arm',arm,
                         '--_case',cid,'--_paper_id',paper_id,'--_result',str(dest)]
                    print(f'[{cid}/{arm}] live isolated subprocess',flush=True)
                    child=subprocess.run(cmd,cwd=repo,env=env,capture_output=True,text=True,timeout=900,check=False)
                    if child.returncode!=0 or not dest.is_file():
                        rows.append({'case_id':cid,'name':SCENARIOS[cid]['name'],'arm':arm,
                                     'steps':[],'errors':['worker failure: '+child.stderr[-1200:]],
                                     'business_passed':False,'safety_passed':False,
                                     'totals': {'input_tokens':None,'output_tokens':None,
                                                'total_tokens':None,'model_calls':0,'tool_calls':0,
                                                'tool_schema_chars_sum':0,'usage_complete':False}})
                    else:
                        rows.append(json.loads(dest.read_text(encoding='utf-8')))
            pairs=[]
            for cid in args.cases:
                aa=next(r for r in rows if r['case_id']==cid and r['arm']=='A')
                bb=next(r for r in rows if r['case_id']==cid and r['arm']=='B')
                pairs.append(summarize_pair(aa,bb))
            # Verify the flag truly changed exposed schemas in at least one
            # comparable MODEL call: a changed env variable is not evidence.
            effect=False
            effective_details=[]
            for cid in args.cases:
                a=next(r for r in rows if r['case_id']==cid and r['arm']=='A')
                b=next(r for r in rows if r['case_id']==cid and r['arm']=='B')
                # Compare schema at the same turn/round, never min(B) vs max(A)
                # across unrelated task phases.
                for sa, sb in zip(a['steps'], b['steps']):
                    ca=sa['usage']['calls']
                    cb=sb['usage']['calls']
                    for model_a, model_b in zip(ca, cb):
                        chars_a=model_a.get('tool_schema_chars')
                        chars_b=model_b.get('tool_schema_chars')
                        if (model_a.get('tool_round') == model_b.get('tool_round')
                                and isinstance(chars_a, int) and isinstance(chars_b, int)
                                and chars_b < chars_a):
                            effect=True
                            effective_details.append({'case_id':cid,'step':sa['index'],
                                                      'round':model_a.get('tool_round'),
                                                      'schema_a':chars_a,'schema_b':chars_b})
            if not effect:
                print('WARNING: A/B schema flag effect not observed in model spans; results are not a proven Dynamic Selection comparison.')
            with helper.db_conn(prepared, readonly=True) as con:
                prepared_after=helper.stable_digest(con,helper.guarded_tables(con))
            if digest!=prepared_after:
                raise RuntimeError('SOURCE FIXTURE CHANGED during A/B; abort')
            comparable_ids=[p['case_id'] for p in pairs if p['comparable']]
            comparable_rows=[r for r in rows if r['case_id'] in comparable_ids]
            comparable_summary=aggregate(comparable_rows) if comparable_rows else None
            report={'suite':'phase2_p1_c_dynamic_tool_schema_ab','utc':datetime.now(timezone.utc).isoformat(),
                    'git_head':git(repo,'rev-parse','HEAD'),
                    'git_branch':git(repo,'branch','--show-current'),
                    'source_db':str(db), 'selected_cases':args.cases,
                    'fixture':{'paper_id':paper_id,'question_count':fixture['count'],
                               'total_score':fixture['total_score_items'],'counts':fixture['counts']},
                    'policy_preflight':p1,'schema_flag_effect_observed':effect,
                    'schema_reduction_evidence':effective_details,
                    'arms':rows,'pairs':pairs,'summary':aggregate(rows),
                    'comparable_ids':comparable_ids,'comparable_summary':comparable_summary}
            paths=save_report(repo,outdir,report)
            print('A/B RESULTS:')
            for p in pairs:
                print(f"  {p['case_id']}: A={p['a_business']}/{p['a_safety']} "
                      f"B={p['b_business']}/{p['b_safety']} input={p['a_input_tokens']}→{p['b_input_tokens']}")
            print('Provider input savings:',report['summary']['input_tokens_savings_pct'])
            print('Flag effect observed:',effect)
            print('JSON:', paths[0])
            print('Markdown:', paths[1])
            regressions=[p for p in pairs if p['business_regression'] or p['safety_regression']]
            return 1 if regressions or not effect or any(not p['comparable'] for p in pairs) else 0
    except Exception as exc:
        print(f'P1-C INFRASTRUCTURE ERROR: {type(exc).__name__}: {exc}',file=sys.stderr)
        traceback.print_exc()
        return 2


if __name__=='__main__':
    raise SystemExit(main())

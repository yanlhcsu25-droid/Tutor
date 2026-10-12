"""Offline unit tests; no Tutor database, provider key, or API calls required."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE = Path(__file__).with_name('token_p1_ab_validation.py')
spec = importlib.util.spec_from_file_location('token_p1_ab_validation', MODULE)
assert spec and spec.loader
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)


def model_span(inp=100, out=10, size=500):
    return {'span_type': 'model_call', 'name': 'llm_completion',
            'input_json': {'n_definitions': 2, 'tool_round': 0,
                           'tool_names': ['read_paper'],
                           'context_metrics': {'tool_schema_chars': size}},
            'output_json': {'actual_usage_available': True, 'model_called': True,
                            'input_tokens': inp, 'output_tokens': out,
                            'total_tokens': inp + out, 'model': 'Qwen/Qwen3.5-35B-A3B',
                            'provider': 'siliconflow'}}


def run_case(arm='A', tokens=100, business=True):
    usage = ab.run_trace_usage({'spans': [model_span(tokens, 10, 500)]})
    row = {'executed': True, 'index': 1, 'status': 'completed',
           'latency_ms': 1000, 'usage': usage,
           'tool_calls': [{'name':'read_paper'}]}
    return {'case_id': 'A01', 'name': 'READ', 'arm': arm,
            'steps': [row], 'business_passed': business,
            'safety_passed': True, 'totals': ab.sum_usage([row])}


def test_trace_counts_physical_model_only():
    x = model_span()
    trace = {'spans': [x, {**x, 'span_type':'tool_call'},
                       {'span_type':'model_call','output_json':{'model_called':False}}],
             'tool_calls_json':[{'tool_name':'read_paper'}]}
    u = ab.run_trace_usage(trace)
    assert (u['model_calls'], u['input_tokens'], u['output_tokens'], u['total_tokens']) == (1,100,10,110)
    assert u['calls'][0]['tool_schema_chars'] == 500


def test_missing_provider_usage_not_estimated():
    broken = model_span()
    del broken['output_json']['actual_usage_available']
    u = ab.run_trace_usage({'spans': [model_span(), broken]})
    assert u['model_calls'] == 2
    assert u['usage_complete'] is False
    assert u['input_tokens'] is None and u['total_tokens'] is None


def test_zero_model_call_counts_zero():
    u = ab.run_trace_usage({'spans': []})
    assert u['model_calls'] == 0 and u['total_tokens'] == 0 and u['usage_complete']


def test_aggregate_pair_savings_and_regression():
    a,b=run_case('A',100),run_case('B',70)
    p=ab.summarize_pair(a,b)
    assert p['comparable'] and p['input_savings_pct']==30
    totals=ab.aggregate([a,b])
    assert totals['input_tokens_savings_pct']==30
    assert totals['delta_model_calls']==0
    b['business_passed']=False
    assert ab.summarize_pair(a,b)['business_regression']
    assert not ab.summarize_pair(a,b)['comparable']


def test_tool_call_error_not_double_count():
    tr = {'spans': [model_span()], 'tool_calls_json': [
        {'tool_name': 'read_paper', 'arguments': {'addresses': []}, 'result': {'ok': True}}]}
    assert len(ab.tool_calls(tr)) == 1
    assert ab.run_trace_usage(tr)['model_calls'] == 1


def test_policy_preflight_detection(tmp_path):
    assert ab.check_p1_files(tmp_path)['ready'] is False
    base=tmp_path/'src/calculus_agent/runtime'
    base.mkdir(parents=True)
    (base/'tool_selection.py').write_text('select',encoding='utf-8')
    (base/'tool_exposure_policy.py').write_text('AGENT_DYNAMIC_TOOL_SELECTION_ENABLED',encoding='utf-8')
    assert ab.check_p1_files(tmp_path)['ready'] is True


def test_report_creation(tmp_path):
    a,b=run_case('A',100),run_case('B',70)
    result={'utc':'2026-10-12T00:00:00Z','git_head':'deadbeef','source_db':'isolated.db',
            'selected_cases':['A01'],'schema_flag_effect_observed':True,
            'summary':ab.aggregate([a,b]),'pairs':[ab.summarize_pair(a,b)],
            'comparable_ids':['A01'],'comparable_summary':ab.aggregate([a,b])}
    js,md=ab.save_report(tmp_path,tmp_path/'reports',result)
    assert js.is_file() and md.is_file()
    assert json.loads(js.read_text(encoding='utf-8'))['summary']['input_tokens_savings_pct'] == 30
    assert 'Strictly comparable pairs only' in md.read_text(encoding='utf-8')


def test_scenario_preconfirmation_safety_boundary():
    assert len(ab.SCENARIOS)==8
    for case in ab.SCENARIOS.values():
        assert case['steps']
        for step in case['steps']:
            if '确认' not in step['message'] or any(x in step['message'] for x in ('不要确认','先让我确认','待确认')):
                if step.get('paper_unchanged'):
                    assert step.get('forbid_confirm')

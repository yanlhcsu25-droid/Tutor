"""Recheck only the reference_used assertion in a saved single-case Dev report.

Does not execute Tutor or Blind. Reuses saved Behavioral Judge scores and
preserves the original report. Requires --execute for one SiliconFlow call.
"""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evaluation.teaching_quality.evaluators.models import JudgeOutput, StructuralAssertionResult
from evaluation.teaching_quality.judge.provider import build_siliconflow_judge_adapter
from evaluation.teaching_quality.evaluators.scoring import score_case
from evaluation.teaching_quality.evaluators.semantic import ModelBackedSemanticAssertionEvaluator
from evaluation.teaching_quality.evaluators.structural import StructuralAssertionGrader
from evaluation.teaching_quality.runners.run_dev_eval import load_dev_cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    path = args.input.expanduser().resolve()
    report = json.loads(path.read_text(encoding='utf-8'))
    if (report.get('kind') != 'diagnostic_real_agent_dev_capture' or
            report.get('blind_executed') is not False or
            report.get('official_benchmark') is not False or
            len(report.get('results', [])) != 1):
        parser.error('Expected one diagnostic Dev-only report, not an official/Blind report')
    row = report['results'][0]
    case_id = row['case_id']
    cases, _ = load_dev_cases()
    case = next((case for case in cases if case['case_id'] == case_id), None)
    if case is None:
        parser.error('Case is not in the frozen Dev split')
    assertions = [a for a in case['expected']['structural_assertions'] if a['type'] == 'reference_used']
    original = row.get('grading', {})
    if len(assertions) != 1 or original.get('status') != 'SCORED' or not original.get('behavioral'):
        parser.error('Expected exactly one reference_used assertion and saved Behavioral scores')
    output = args.output or path.with_name('reference-rejudge.json')
    if output.exists():
        parser.error('Refusing to overwrite an existing result')
    if not args.execute:
        print(f'DEV={case_id} / Saved agent output present / Original result remains unchanged')
        print('PREVIEW ONLY; pass --execute for exactly one Semantic Judge call, no Tutor call')
        return
    from calculus_agent.config import get_settings

    adapter = build_siliconflow_judge_adapter(get_settings(), max_tokens=2048)
    evaluator = ModelBackedSemanticAssertionEvaluator(adapter)
    changed = StructuralAssertionGrader(semantic_evaluator=evaluator).grade(
        assertions[0], row['actual_output'], case['setup']
    )
    observations = [
        changed if item['assertion_id'] == assertions[0]['id'] else StructuralAssertionResult.model_validate(item)
        for item in original['structural']
    ]
    if not any(x.assertion_id == assertions[0]['id'] for x in observations):
        parser.error('Reference assertion not present in original report')
    judge = JudgeOutput.model_validate(original['behavioral'])
    new_score = score_case(observations, judge, case['expected']['behavioral_rubric']['axes'])
    result = {
        'kind': 'diagnostic_saved_reference_rejudge',
        'created_at': datetime.now(UTC).isoformat(),
        'case_id': case_id,
        'original_report': str(path),
        'original_report_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'tutor_executed': False,
        'blind_executed': False,
        'official_benchmark': False,
        'setup_fixture_loaded': report.get('setup_fixture_loaded', False),
        'reference_used_before': next((x for x in original['structural'] if x['assertion_id'] == assertions[0]['id']), None),
        'reference_used_after': changed.model_dump(mode='json'),
        'original_score': original['score'],
        'rejudged_score': new_score.model_dump(mode='json'),
        'behavioral_reused': True,
        'provider': adapter.provenance(),
        'semantic_prompt_sha256': hashlib.sha256(
            (Path(__file__).resolve().parents[1] / 'prompts/semantic_assertion_judge.md').read_bytes()
        ).hexdigest(),
        'note': 'Diagnostic, unseeded. Semantic reference assertion re-evaluated; Behavioral scores reused without model call.',
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'REPORT={output}')
    print(f'REFERENCE_USED={changed.passed} SCORE={new_score.weighted_score} STATUS={new_score.status}')


if __name__ == '__main__':
    main()

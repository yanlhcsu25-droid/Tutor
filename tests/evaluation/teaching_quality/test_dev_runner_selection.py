from evaluation.teaching_quality.runners.run_dev_eval import load_dev_cases


def test_only_24_frozen_dev_cases_selected():
    cases, manifest = load_dev_cases()
    dev = {case['case_id'] for case in cases}
    blind = {entry['case_id'] for entry in manifest['splits']['blind']['cases']}
    assert len(cases) == 24
    assert len(blind) == 12
    assert dev.isdisjoint(blind)
    assert all(case['human_review']['status'] == 'approved' for case in cases)


def test_smoke_case_is_dev():
    cases, _ = load_dev_cases()
    assert 'TQ-CQ-006' in {case['case_id'] for case in cases}

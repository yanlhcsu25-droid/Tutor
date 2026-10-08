from pathlib import Path

import yaml

from tests.evals.teaching_quality.grader.behavioral import render_judge_prompt

ROOT = Path(__file__).resolve().parents[1]


def test_semantic_fixture_changes_keep_assertion_boundaries_separate():
    payload = yaml.safe_load((ROOT / "calibration/semantic_contrast_v1.yaml").read_text())
    samples = {sample["sample_id"]: sample for sample in payload["samples"]}
    assert samples["SC-RC-03"]["expected_passed"] is True
    assert "3题仅答对1题" in samples["SC-SG-01"]["actual_output"]["response"]["text"]
    assert samples["SC-SG-01"]["expected_passed"] is True
    assert samples["SC-ID-03"]["expected_passed"] is True


def test_semantic_prompt_defines_disclosure_only_and_basic_cause_analysis():
    prompt = (ROOT / "prompts/semantic_assertion_judge.md").read_text()
    assert "counts as basic cause analysis" in prompt
    assert "evaluate disclosure only" in prompt
    assert "no_fabricated_items" in prompt
    assert "constraint_preserved" in prompt


def test_behavioral_prompt_requires_independent_axis_evidence(approved_case):
    prompt = render_judge_prompt(approved_case, "fixed output")
    assert "AXIS_INDEPENDENCE" in prompt
    assert "Do not automatically lower every axis" in prompt
    assert "axis-specific consequence" in prompt


def test_suite_prompts_and_rubrics_keep_axes_distinct():
    consultation_prompt = (ROOT / "prompts/consultation_judge.md").read_text()
    teaching_prompt = (ROOT / "prompts/teaching_design_judge.md").read_text()
    paper_prompt = (ROOT / "prompts/paper_quality_judge.md").read_text()
    assert "concrete and executable yet educationally wrong" in consultation_prompt
    assert "fits the available minutes" in teaching_prompt
    assert "complete question stems/solutions" in paper_prompt
    assert "Do not apply an automatic score cap" in paper_prompt


def test_blind_manifest_hash_is_unchanged():
    digest = (ROOT / "manifests/split_v1.yaml.sha256").read_text().split()[0]
    assert digest == "113e00345600204a5d5de09bf4255200f83886cc7841d05532ac5e35ab4007a4"

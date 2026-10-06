from pathlib import Path

import yaml

from calculus_agent.evaluations.holdout_manifest import validate_holdout_manifest


DATASET = Path(
    "tests/evals/staging/tutor_workflow_eval_300_v3_1/"
    "tutor_workflow_eval_300_v3_1.yaml"
)
MANIFEST = Path("tests/evals/manifests/holdout_v3_1_69.yaml")


def test_frozen_holdout_manifest_selects_all_69_approved_unique_cases():
    report = validate_holdout_manifest(
        MANIFEST,
        [DATASET],
        verify_current_components=False,
    )

    assert report["valid"], report["errors"]
    assert report["selected_case_count"] == 69
    assert report["approved_case_count"] == 69
    assert report["excluded_duplicate_count"] == 31


def test_frozen_holdout_manifest_records_first_run_authorization():
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["status"] == "frozen"
    assert manifest["execution_authorized"] is True
    assert manifest["execution"]["max_tool_rounds"] == 8
    assert manifest["execution"]["timeout_seconds"] == 120
    assert manifest["execution"]["retry_policy"]["provider_retries"] == 0

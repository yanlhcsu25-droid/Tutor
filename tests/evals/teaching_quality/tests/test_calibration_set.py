from pathlib import Path

import yaml

from tests.evals.teaching_quality.runner.validate_calibration_set import (
    CALIBRATION_SET,
    validate_calibration_set,
)


def test_calibration_set_is_dev_only_human_labeled_and_complete():
    validate_calibration_set()
    payload = yaml.safe_load(Path(CALIBRATION_SET).read_text())
    assert payload["case_count"] == 7
    assert payload["sample_count"] == 21
    assert payload["suite_counts"] == {
        "consultation": 3,
        "paper_quality": 2,
        "teaching_design": 2,
    }
    assert payload["status"] == "approved_dev_calibration"
    assert payload["policy"] == {
        "blind_cases_forbidden": True,
        "model_execution_performed": False,
        "samples_per_case": 3,
        "human_review_completed": True,
    }
    provenance = yaml.safe_load(
        (Path(CALIBRATION_SET).parent / "calibration_set_v1.provenance.yaml").read_text()
    )
    assert provenance["previous_artifact"]["sha256"] == (
        "60682914037c102f9549b6313e6542943a4a6b93f0b7152fa661c031ccd75cb4"
    )
    assert provenance["review_summary"] == {
        "sample_count": 21,
        "overall_bands_approved_unchanged": 21,
        "axis_ranges_approved_unchanged": 10,
        "axis_ranges_revised": 11,
        "actual_outputs_rewritten": 0,
    }

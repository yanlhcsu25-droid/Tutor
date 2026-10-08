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
    assert payload["status"] == "review_required_dev_only"
    assert payload["policy"] == {
        "blind_cases_forbidden": True,
        "model_execution_performed": False,
        "samples_per_case": 3,
        "candidate_labels_human_review_required": True,
    }

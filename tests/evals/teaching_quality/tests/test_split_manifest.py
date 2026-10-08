from pathlib import Path

import yaml

from tests.evals.teaching_quality.runner.validate_split_manifest import (
    MANIFEST,
    validate_split_manifest,
)


def test_frozen_split_manifest_hashes_and_family_isolation():
    validate_split_manifest()
    manifest = yaml.safe_load(Path(MANIFEST).read_text())
    dev = manifest["splits"]["dev"]
    blind = manifest["splits"]["blind"]
    assert dev["case_count"] == 24
    assert blind["case_count"] == 12
    assert dev["suite_counts"] == {
        "consultation": 7, "paper_quality": 9, "teaching_design": 8,
    }
    assert blind["suite_counts"] == {
        "consultation": 4, "paper_quality": 4, "teaching_design": 4,
    }
    assert {case["family_id"] for case in dev["cases"]}.isdisjoint(
        {case["family_id"] for case in blind["cases"]}
    )

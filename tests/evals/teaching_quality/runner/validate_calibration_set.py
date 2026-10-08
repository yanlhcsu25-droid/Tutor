"""Validate the human-labeled Dev calibration set without invoking a model."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_SET = ROOT / "calibration/calibration_set_v1.yaml"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_calibration_set(
    root: Path = ROOT, calibration_path: Path = CALIBRATION_SET
) -> None:
    calibration = yaml.safe_load(calibration_path.read_text())
    if calibration["status"] != "review_required_dev_only":
        raise ValueError("calibration reference labels must remain review-required")
    if calibration["policy"].get("candidate_labels_human_review_required") is not True:
        raise ValueError("calibration labels require explicit human review")
    manifest_path = root.parents[2] / calibration["source_manifest"]
    if sha256(manifest_path) != calibration["source_manifest_sha256"]:
        raise ValueError("source split manifest hash mismatch")
    manifest = yaml.safe_load(manifest_path.read_text())
    dev = {entry["case_id"]: entry for entry in manifest["splits"]["dev"]["cases"]}
    blind_ids = {entry["case_id"] for entry in manifest["splits"]["blind"]["cases"]}

    case_map = {}
    for record in manifest["dataset"]["files"]:
        path = root.parents[2] / record["path"]
        for case in yaml.safe_load(path.read_text())["cases"]:
            case_map[case["case_id"]] = case

    entries = calibration["cases"]
    ids = [entry["case_id"] for entry in entries]
    if len(ids) != len(set(ids)) or len(ids) != calibration["case_count"]:
        raise ValueError("calibration case IDs are duplicated or count is wrong")
    if set(ids) & blind_ids:
        raise ValueError("calibration set contains a frozen Blind case")
    if not set(ids) <= set(dev):
        raise ValueError("calibration set contains a non-Dev case")

    sample_ids: set[str] = set()
    for entry in entries:
        case_id = entry["case_id"]
        source = dev[case_id]
        if entry["source_split"] != "dev":
            raise ValueError(f"{case_id}: source_split must be dev")
        if entry["family_id"] != source["family_id"] or entry["suite"] != source["suite"]:
            raise ValueError(f"{case_id}: frozen manifest metadata mismatch")
        axes = set(case_map[case_id]["expected"]["behavioral_rubric"]["axes"])
        samples = entry["samples"]
        if {sample["band"] for sample in samples} != {"strong", "borderline", "poor"}:
            raise ValueError(f"{case_id}: requires strong/borderline/poor samples")
        if len(samples) != calibration["policy"]["samples_per_case"]:
            raise ValueError(f"{case_id}: unexpected sample count")
        for sample in samples:
            sample_id = sample["sample_id"]
            if sample_id in sample_ids:
                raise ValueError(f"duplicate sample_id: {sample_id}")
            sample_ids.add(sample_id)
            if not str(sample["actual_output"]).strip():
                raise ValueError(f"{sample_id}: empty output")
            expected = sample["expected_quality"]
            if expected["overall_band"] != sample["band"]:
                raise ValueError(f"{sample_id}: band mismatch")
            ranges = expected["expected_axis_ranges"]
            if set(ranges) != axes:
                raise ValueError(f"{sample_id}: axis ranges do not match case-local axes")
            if any(
                len(limits) != 2 or not (1 <= limits[0] <= limits[1] <= 5)
                for limits in ranges.values()
            ):
                raise ValueError(f"{sample_id}: invalid axis range")

    if len(sample_ids) != calibration["sample_count"]:
        raise ValueError("calibration sample count mismatch")
    suites = dict(sorted(Counter(entry["suite"] for entry in entries).items()))
    if suites != calibration["suite_counts"]:
        raise ValueError("calibration suite count mismatch")
    if calibration["policy"]["model_execution_performed"] is not False:
        raise ValueError("calibration specification must not claim model execution")
    sidecar = calibration_path.with_suffix(calibration_path.suffix + ".sha256")
    if sha256(calibration_path) != sidecar.read_text().split()[0]:
        raise ValueError("calibration set hash mismatch")


if __name__ == "__main__":
    validate_calibration_set()
    print("OK: 7 Dev cases / 21 review-required fixed outputs; no Blind cases or model runs")

"""Validate semantic contrast fixtures without invoking a model."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRAST_SET = ROOT / "calibration/semantic_contrast_v1.yaml"
TYPES = {
    "required_content", "forbidden_content", "source_grounded",
    "reference_used", "insufficiency_disclosed",
}


def validate_semantic_contrast_set(path: Path = CONTRAST_SET) -> None:
    payload = yaml.safe_load(path.read_text())
    samples = payload["samples"]
    if payload["status"] != "review_required_semantic_contrast":
        raise ValueError("semantic contrast labels must remain review-required")
    if len(samples) != 15 or payload["sample_count"] != 15:
        raise ValueError("semantic contrast set must contain 15 samples")
    ids = [sample["sample_id"] for sample in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate semantic contrast sample_id")
    counts = Counter(sample["assertion_type"] for sample in samples)
    if set(counts) != TYPES or any(count != 3 for count in counts.values()):
        raise ValueError("each semantic assertion type requires exactly three samples")
    for assertion_type in TYPES:
        kinds = {
            sample["example_kind"] for sample in samples
            if sample["assertion_type"] == assertion_type
        }
        if kinds != {"clear_pass", "clear_fail", "boundary"}:
            raise ValueError(f"{assertion_type}: missing pass/fail/boundary coverage")
    for sample in samples:
        if sample["assertion"]["type"] != sample["assertion_type"]:
            raise ValueError(f"{sample['sample_id']}: assertion type mismatch")
        if not isinstance(sample["expected_passed"], bool):
            raise ValueError(f"{sample['sample_id']}: expected_passed must be boolean")
    if payload["policy"] != {
        "binary_labels_only": True,
        "blind_cases_used": False,
        "model_execution_performed": False,
    }:
        raise ValueError("semantic contrast policy mismatch")
    sidecar = path.with_suffix(path.suffix + ".sha256")
    expected_hash = sidecar.read_text().split()[0]
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("semantic contrast set hash mismatch")


if __name__ == "__main__":
    validate_semantic_contrast_set()
    print("OK: 15 review-required semantic contrast samples; no Blind data or model runs")

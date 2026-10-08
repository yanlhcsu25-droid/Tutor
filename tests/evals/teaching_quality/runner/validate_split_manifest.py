"""Validate the frozen family-isolated split without executing any case."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifests/split_v1.yaml"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_split_manifest(root: Path = ROOT, manifest_path: Path = MANIFEST) -> None:
    manifest = yaml.safe_load(manifest_path.read_text())
    if manifest["status"] != "frozen":
        raise ValueError("split manifest must be frozen")
    if manifest["splits"]["blind"]["status"] != "frozen_do_not_run":
        raise ValueError("Blind split must be frozen and marked do-not-run")

    dataset_records = manifest["dataset"]["files"]
    for record in dataset_records:
        path = root.parents[2] / record["path"]
        if sha256(path) != record["sha256"]:
            raise ValueError(f"dataset hash mismatch: {record['path']}")
    aggregate = "".join(
        f"{record['path']}:{record['sha256']}\n" for record in dataset_records
    ).encode()
    if hashlib.sha256(aggregate).hexdigest() != manifest["dataset"]["sha256"]:
        raise ValueError("aggregate dataset hash mismatch")

    cases = {}
    for record in dataset_records:
        path = root.parents[2] / record["path"]
        for case in yaml.safe_load(path.read_text())["cases"]:
            if case["case_id"] in cases:
                raise ValueError(f"duplicate dataset case_id: {case['case_id']}")
            cases[case["case_id"]] = case

    split_ids: dict[str, set[str]] = {}
    split_families: dict[str, set[str]] = {}
    for split_name in ("dev", "blind"):
        split = manifest["splits"][split_name]
        entries = split["cases"]
        ids = {entry["case_id"] for entry in entries}
        if len(ids) != len(entries) or len(ids) != split["case_count"]:
            raise ValueError(f"{split_name}: duplicate IDs or count mismatch")
        for entry in entries:
            case = cases.get(entry["case_id"])
            if case is None:
                raise ValueError(f"{split_name}: unknown case {entry['case_id']}")
            if entry["family_id"] != case["family_id"] or entry["suite"] != case["suite"]:
                raise ValueError(f"{split_name}: metadata mismatch for {entry['case_id']}")
        families = {entry["family_id"] for entry in entries}
        suites = Counter(entry["suite"] for entry in entries)
        if len(families) != split["family_count"]:
            raise ValueError(f"{split_name}: family count mismatch")
        if dict(sorted(suites.items())) != split["suite_counts"]:
            raise ValueError(f"{split_name}: suite count mismatch")
        split_ids[split_name] = ids
        split_families[split_name] = families

    if split_ids["dev"] & split_ids["blind"]:
        raise ValueError("case IDs cross Dev/Blind")
    if split_families["dev"] & split_families["blind"]:
        raise ValueError("families cross Dev/Blind")
    if split_ids["dev"] | split_ids["blind"] != set(cases):
        raise ValueError("split does not cover the approved dataset exactly")
    if len(cases) != manifest["dataset"]["case_count"]:
        raise ValueError("dataset case count mismatch")
    if len({case["family_id"] for case in cases.values()}) != manifest["dataset"]["family_count"]:
        raise ValueError("dataset family count mismatch")

    sidecar = manifest_path.with_suffix(manifest_path.suffix + ".sha256")
    expected_manifest_hash = sidecar.read_text().split()[0]
    if sha256(manifest_path) != expected_manifest_hash:
        raise ValueError("manifest hash mismatch")


if __name__ == "__main__":
    validate_split_manifest()
    print("OK: frozen 24 Dev / 12 Blind split; family isolation and hashes verified")

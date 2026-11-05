from pathlib import Path

import yaml

from calculus_agent.evaluations.smoke_manifest import dataset_sha, validate_manifest


def test_frozen_smoke_manifest_requires_exact_distribution_and_matching_sha(tmp_path: Path):
    difficulties = ["easy"] * 6 + ["medium"] * 9 + ["hard"] * 5
    dataset = tmp_path / "dev.yaml"
    dataset.write_text(yaml.safe_dump({
        "cases": [
            {"id": f"C-{index:02d}", "difficulty": difficulty}
            for index, difficulty in enumerate(difficulties, 1)
        ],
    }), encoding="utf-8")
    manifest = tmp_path / "smoke.yaml"
    manifest.write_text(yaml.safe_dump({
        "status": "frozen",
        "split": "development",
        "dataset_sha": dataset_sha([dataset]),
        "execution": {"provider": "test-provider", "model": "test-model"},
        "versions": {"fixture": "fixture-v1", "grader": "grader-v1"},
        "freeze_lock": {
            "human_reviewed": True,
            "code_git_sha": "a" * 40,
        },
        "budget": {"max_model_calls": 100, "max_total_tokens": 500_000},
        "cases": [
            {"case_id": f"C-{index:02d}", "difficulty": difficulty}
            for index, difficulty in enumerate(difficulties, 1)
        ],
    }), encoding="utf-8")

    report = validate_manifest(manifest, [dataset])

    assert report["valid"]
    assert report["difficulty_distribution"] == {"easy": 6, "medium": 9, "hard": 5}


def test_frozen_manifest_fails_without_reproducibility_lock(tmp_path: Path):
    difficulties = ["easy"] * 6 + ["medium"] * 9 + ["hard"] * 5
    dataset = tmp_path / "dev.yaml"
    dataset.write_text(yaml.safe_dump({
        "cases": [
            {"id": f"C-{index:02d}", "difficulty": difficulty}
            for index, difficulty in enumerate(difficulties, 1)
        ],
    }), encoding="utf-8")
    manifest = tmp_path / "smoke.yaml"
    manifest.write_text(yaml.safe_dump({
        "status": "frozen",
        "split": "development",
        "dataset_sha": dataset_sha([dataset]),
        "budget": {"max_model_calls": 1, "max_total_tokens": 1},
        "cases": [
            {"case_id": f"C-{index:02d}", "difficulty": difficulty}
            for index, difficulty in enumerate(difficulties, 1)
        ],
    }), encoding="utf-8")

    report = validate_manifest(manifest, [dataset])

    assert not report["valid"]
    assert any("execution.provider" in error for error in report["errors"])
    assert any("code_git_sha" in error for error in report["errors"])


def test_draft_manifest_fails_closed(tmp_path: Path):
    dataset = tmp_path / "dev.yaml"
    dataset.write_text("cases: []\n", encoding="utf-8")
    manifest = tmp_path / "smoke.yaml"
    manifest.write_text("status: blocked\nsplit: development\ncases: []\n", encoding="utf-8")

    report = validate_manifest(manifest, [dataset])

    assert not report["valid"]
    assert "manifest status must be frozen" in report["errors"]
    assert any("20 cases" in error for error in report["errors"])

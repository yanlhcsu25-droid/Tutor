from pathlib import Path

import pytest
import yaml


@pytest.fixture
def approved_case():
    path = Path(__file__).resolve().parents[1] / "cases" / "consultation_v1.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return next(
        case
        for case in data["cases"]
        if case.get("human_review", {}).get("status") == "approved"
    )

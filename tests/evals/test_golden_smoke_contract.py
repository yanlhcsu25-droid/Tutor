from collections import Counter
from pathlib import Path

import yaml


GOLDEN = Path(__file__).parent / "cases" / "golden_smoke_v1.yaml"
STAGING = (
    Path(__file__).parent
    / "staging"
    / "tutor_workflow_eval_300_v2"
    / "tutor_workflow_eval_300_v2.yaml"
)


def test_golden_smoke_has_reviewable_business_oracles_and_dev_sources():
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    staging = yaml.safe_load(STAGING.read_text(encoding="utf-8"))
    cases = golden["cases"]
    sources = {case["id"]: case for case in staging["cases"]}

    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20
    assert Counter(case["difficulty"] for case in cases) == {
        "easy": 6, "medium": 9, "hard": 5,
    }
    assert Counter(case["category"] for case in cases) == {
        "generation": 4,
        "pending": 4,
        "paper": 6,
        "teaching": 3,
        "ambiguity": 1,
        "multi_turn": 2,
    }
    assert all(sources[case["source_case_id"]]["split"] == "dev" for case in cases)
    assert all(
        set(case["expected"]) != {"status"}
        for case in cases
        if case["subtype"] != "consultation"
    )


def test_golden_cancel_and_read_cases_have_authoritative_assertions():
    cases = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))["cases"]
    cancel = next(case for case in cases if case["id"] == "SMOKE-PENDING-02")
    acceptance = next(
        grader for grader in cancel["graders"] if grader["type"] == "acceptance"
    )

    assert cancel["expected"]["pending_generation"] is None
    assert acceptance["required_tools"] == ["discard_pending_plan"]

    reads = [case for case in cases if case["id"].startswith("SMOKE-READ-")]
    assert all("response_facts" in case["expected"] for case in reads)
    assert all("paper_db" in case["expected"] for case in reads)

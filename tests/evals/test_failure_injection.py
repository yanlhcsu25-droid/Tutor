from pathlib import Path

from tests.evals.case_loader import load_eval_suite
from tests.evals.runner import run_case


CASE_FILE = Path(__file__).parent / "cases" / "reliability_failure_injection_v0.yaml"


def test_failure_injection_suite_fails_closed_without_live_model():
    suite = load_eval_suite(CASE_FILE)
    assert len(suite.cases) == 7

    results = [run_case(case) for case in suite.cases]

    assert all(result["passed"] for result in results), [
        (result["case_id"], result.get("graders"), result.get("error"))
        for result in results if not result["passed"]
    ]

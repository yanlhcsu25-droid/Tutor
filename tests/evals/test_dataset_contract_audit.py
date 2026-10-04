from pathlib import Path

import yaml

from tests.evals.dataset_contract_audit import audit_paths, markdown


def test_audit_classifies_valid_and_unsupported_cases(tmp_path: Path):
    suite = tmp_path / "cases.yaml"
    suite.write_text(
        """
suite:
  split: development
cases:
  - id: OK-1
    category: generation
    turns: [{user: "第一章出8题，先看方案"}]
    expected: {status: waiting_confirmation, pending_generation: {question_count: 8}}
    graders: [state, acceptance]
  - id: BAD-1
    text: "route me"
    expected: DIRECT_ACTION
""",
        encoding="utf-8",
    )

    report = audit_paths([suite])

    assert report["summary"]["case_count"] == 2
    assert report["summary"]["dispositions"] == {
        "runner_unsupported": 1,
        "valid": 1,
    }
    assert "Cases found: **2** / expected **300**" in markdown(report)


def test_audit_counts_case_level_splits(tmp_path: Path):
    suite = tmp_path / "cases.yaml"
    cases = "\n".join(
        f"  - id: DEV-{index}\n    split: dev\n    turns: [{{user: ok}}]\n    expected: {{status: completed}}\n    graders: [state]"
        for index in range(200)
    )
    cases += "\n" + "\n".join(
        f"  - id: HOLDOUT-{index}\n    split: holdout_candidate\n    turns: [{{user: ok}}]\n    expected: {{status: completed}}\n    graders: [state]"
        for index in range(100)
    )
    suite.write_text(f"cases:\n{cases}\n", encoding="utf-8")

    report = audit_paths([suite])

    assert report["summary"]["split_counts"] == {"dev": 200, "holdout_candidate": 100}
    assert report["summary"]["required_split_matches"] is True


def test_audit_counts_cross_split_category_subtype_families(tmp_path: Path):
    suite = tmp_path / "cases.yaml"
    suite.write_text(
        """
cases:
  - id: DEV-1
    split: dev
    category: paper
    subtype: read
    turns: [{user: ok}]
    expected: {status: completed}
    graders: [state]
  - id: HOLDOUT-1
    split: holdout_candidate
    category: paper
    subtype: read
    turns: [{user: ok}]
    expected: {status: completed}
    graders: [state]
""",
        encoding="utf-8",
    )

    assert audit_paths([suite])["summary"]["cross_split_category_subtype_families"] == 1


def test_audit_does_not_treat_canceling_pending_confirmation_as_confirmation(tmp_path: Path):
    suite = tmp_path / "cases.yaml"
    suite.write_text(
        """
cases:
  - id: CANCEL-1
    turns: [{user: "把待确认方案取消。"}]
    expected: {status: completed, pending_generation: null}
    graders:
      - state
      - {type: acceptance, required_tools: [discard_pending_plan]}
      - {type: workflow_safety, paper_unchanged: true}
""",
        encoding="utf-8",
    )

    assert audit_paths([suite])["cases"][0]["disposition"] == "valid"


def test_v3_1_has_only_remaining_human_review_audit_blockers():
    path = Path(
        "tests/evals/staging/tutor_workflow_eval_300_v3_1/"
        "tutor_workflow_eval_300_v3_1.yaml"
    )

    report = audit_paths([path])
    findings = [
        finding["message"]
        for case in report["cases"]
        for finding in case["findings"]
    ]

    assert report["summary"]["required_split_matches"] is True
    assert report["summary"]["cross_split_category_subtype_families"] == 0
    assert report["summary"]["dispositions"] == {
        "ambiguous_label": 299,
        "valid": 1,
    }
    assert findings == ["manual review status is 'pending'"] * 299


def test_eval_265_is_a_grounded_read_only_current_paper_case():
    path = Path(
        "tests/evals/staging/tutor_workflow_eval_300_v3_1/"
        "tutor_workflow_eval_300_v3_1.yaml"
    )
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]
    case = next(item for item in cases if item["id"] == "EVAL-265")
    acceptance = next(
        grader for grader in case["graders"]
        if isinstance(grader, dict) and grader.get("type") == "acceptance"
    )

    assert case["setup"]["current_paper"] == {
        "question_count": 10,
        "total_score": 100,
    }
    assert case["expected"] == {
        "status": "completed",
        "paper_db": {"persisted": True, "question_count": 10, "total_score": 100},
        "response_facts": {"question_count": 10},
        "pending_paper_change": None,
    }
    assert acceptance["required_tools"] == ["read_paper"]
    assert "preview_paper_changes" in acceptance["forbidden_tools"]
    assert "confirm_paper_changes" in acceptance["forbidden_tools"]


def test_audit_flags_pending_review_and_ambiguous_paper_address(tmp_path: Path):
    suite = tmp_path / "cases.yaml"
    suite.write_text(
        """
cases:
  - id: REVIEW-1
    turns: [{user: "把第三题换简单一点"}]
    expected: {status: waiting_confirmation, pending_paper_change: null}
    graders: [state]
    review: {status: pending}
""",
        encoding="utf-8",
    )

    case = audit_paths([suite])["cases"][0]

    assert case["disposition"] == "ambiguous_label"
    assert {finding["message"] for finding in case["findings"]} == {
        "manual review status is 'pending'",
        "unqualified 第N题 addressing is not labelled needs_clarification",
    }

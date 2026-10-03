from types import SimpleNamespace

from tests.evals.graders.content_consistency_grader import grade_content_consistency


def _case(*, preserve=None, rules=None):
    return SimpleNamespace(preserve=preserve or [], business_rules=rules or {})


def _snapshot(*, score=100, item_scores=(50, 50), chapters=("c1", "c1")):
    return {
        "paper": {
            "paper": [{"id": "p1", "version": 1, "total_score": score}],
            "paper_item": [
                {"paper_id": "p1", "question_id": f"q{i}", "section": "计算题", "score": value}
                for i, value in enumerate(item_scores, 1)
            ],
            "question_chapters": {
                f"q{i}": chapter for i, chapter in enumerate(chapters, 1)
            },
            "active_pointer": ["p1", "p1"],
        },
        "teaching_design": {"teaching_design_version": []},
    }


def test_plan_count_and_score_sums_must_be_consistent():
    actual = {"pending_generation": {
        "question_count": 3,
        "total_score": 100,
        "question_type_requirements": [
            {"question_type": "选择题", "count": 2, "score_each": 20, "total_score": 40},
            {"question_type": "计算题", "count": 2, "score_each": 20, "total_score": 40},
        ],
    }}

    result = grade_content_consistency(_case(), actual)

    assert not result["passed"]
    assert any("section count sum=4" in error for error in result["errors"])
    assert any("section score sum=80.0" in error for error in result["errors"])


def test_paper_item_scores_and_reply_must_match_database():
    actual = {"evaluated_turns": [{
        "turn": 1,
        "business_state_after": _snapshot(score=100, item_scores=(40, 40)),
        "result": {"message": "试卷共3题，总分120分。"},
    }]}

    result = grade_content_consistency(_case(), actual)

    assert not result["passed"]
    assert any("item score sum=80" in error for error in result["errors"])
    assert any("reply question count=3, persisted=2" in error for error in result["errors"])
    assert any("reply total score=120" in error for error in result["errors"])


def test_no_cross_chapter_and_no_repeat_use_persisted_items():
    snapshot = _snapshot(chapters=("c1", "c2"))
    actual = {"evaluated_turns": [{
        "turn": 1, "business_state_after": snapshot, "result": {"message": ""},
    }]}

    result = grade_content_consistency(_case(preserve=["no_cross_chapter"]), actual)
    assert not result["passed"]
    assert any("cross chapters" in error for error in result["errors"])

    snapshot["paper"]["paper_item"][1]["question_id"] = "q1"
    duplicate = grade_content_consistency(_case(), actual)
    assert any("duplicate question ids" in error for error in duplicate["errors"])


def test_teaching_design_assessment_plan_is_checked():
    snapshot = _snapshot()
    snapshot["teaching_design"]["teaching_design_version"] = [{
        "id": "td1",
        "design_json": {"assessment_plan": {
            "question_count": 3,
            "total_score": 100,
            "question_type_requirements": [
                {"question_type": "计算题", "count": 2, "score_each": 40},
            ],
        }},
    }]

    result = grade_content_consistency(_case(), {"evaluated_turns": [{
        "turn": 1, "business_state_after": snapshot, "result": {"message": ""},
    }]})

    assert not result["passed"]
    assert any("teaching_design td1.assessment_plan" in error for error in result["errors"])

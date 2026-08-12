from time import perf_counter

import pytest

from calculus_agent.models import Question, QuestionDraft, new_id
from calculus_agent.papers.selector import compose_paper
from calculus_agent.schemas import PaperBlueprint, SectionRequirement


@pytest.mark.parametrize("size,limit", [(100, 1.0), (1000, 3.0), (3000, 5.0)])
def test_cp_sat_composition_scales_to_real_question_banks(session, size, limit):
    values = []
    for number in range(size):
        draft_id, question_id = new_id(), new_id()
        question_type = "选择题" if number % 2 == 0 else "解答题"
        values.extend([
            QuestionDraft(
                id=draft_id, source_name="performance", source_item_id=str(number), variant=1,
                subject="初中数学", grade="八年级", question_type=question_type,
                question_text=f"性能题 {number}", reference_answers_json=["1"],
                normalized_fingerprint=f"{number:064x}", status="approved",
            ),
            Question(
                id=question_id, draft_id=draft_id, question_text=f"性能题 {number}",
                grade="八年级", question_type=question_type,
                final_answer="1", solution_json={"solution_steps":["解析"]},
                verification_status="verified", review_status="approved",
            ),
        ])
    session.add_all(values)
    session.flush()
    blueprint = PaperBlueprint(
        grade="八年级", total_questions=10, total_score=100,
        sections=[
            SectionRequirement(question_type="选择题", count=5, score_per_question=5, total_score=25),
            SectionRequirement(question_type="解答题", count=5, score_per_question=15, total_score=75),
        ], seed=7,
    )
    started = perf_counter()
    result = compose_paper(session, blueprint)
    elapsed = perf_counter() - started
    assert result.feasible is True
    assert elapsed < limit, f"{size}题组卷耗时{elapsed:.3f}s，要求<{limit}s"

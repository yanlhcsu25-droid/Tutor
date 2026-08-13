from sqlalchemy import func, select

from calculus_agent.demo import QUESTIONS, seed_demo_questions
from calculus_agent.models import Question
from calculus_agent.papers.selector import compose_paper
from calculus_agent.schemas import KnowledgeQuota, PaperBlueprint


def test_demo_seed_is_idempotent_but_never_enters_formal_paper_candidates(session):
    assert seed_demo_questions(session) == (len(QUESTIONS), 0)
    assert seed_demo_questions(session) == (0, len(QUESTIONS))
    assert session.scalar(select(func.count()).select_from(Question)) == len(QUESTIONS)

    paper = compose_paper(
        session,
        PaperBlueprint(
            total_questions=10,
            total_score=100,
            question_type_counts={"选择题": 4, "填空题": 2, "解答题": 4},
            knowledge_quotas=[KnowledgeQuota(name="一次函数", count=5)],
        ),
    )

    assert paper.feasible is False
    assert paper.items == []

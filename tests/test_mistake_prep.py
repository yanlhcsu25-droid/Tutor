from calculus_agent.models import KnowledgeNode, Question, QuestionDraft, QuestionKnowledgeLink
from calculus_agent.prep.mistakes import create_mistake_prep, get_mistake_prep
from calculus_agent.schemas import MistakePrepCreate


def _question(session, *, number: int, knowledge: KnowledgeNode) -> Question:
    draft = QuestionDraft(
        source_name="mistake-prep-test",
        source_item_id=str(number),
        variant=1,
        subject="初中数学",
        grade="八年级",
        question_type="解答题",
        question_text=f"一次函数练习 {number}",
        reference_answers_json=[str(number)],
        normalized_fingerprint=str(number) * 64,
        status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        grade="八年级",
        question_type="解答题",
        final_answer=str(number),
        solution_json={"solution_steps": [f"解析 {number}"]},
        verification_status="dataset_reference",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    session.add(
        QuestionKnowledgeLink(
            question_id=question.id,
            knowledge_node_id=knowledge.id,
            relation_type="primary_concept",
        )
    )
    session.flush()
    return question


def test_creates_mistake_prep_and_ranks_matching_questions(session):
    knowledge = KnowledgeNode(
        node_type="concept",
        name="一次函数",
        normalized_name="一次函数",
        review_status="approved",
    )
    session.add(knowledge)
    session.flush()
    closest = _question(session, number=1, knowledge=knowledge)
    _question(session, number=2, knowledge=knowledge)

    result = create_mistake_prep(
        session,
        MistakePrepCreate(
            question_text="学生原始错题",
            final_answer="2",
            solution_text="根据一次函数定义求解。",
            error_reason="混淆斜率和截距",
            question_type="解答题",
            knowledge_names=["一次函数"],
            match_count=2,
        ),
    )

    assert len(result.matches) == 2
    assert all(item.question_type == "解答题" for item in result.matches)
    assert all("一次函数" in item.knowledge for item in result.matches)
    restored = get_mistake_prep(session, result.id)
    assert restored is not None
    assert [item.question_id for item in restored.matches] == [
        item.question_id for item in result.matches
    ]

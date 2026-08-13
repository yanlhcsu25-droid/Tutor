from calculus_agent.models import KnowledgeNode, Question, QuestionDraft, QuestionKnowledgeLink
from calculus_agent.papers.selector import compose_paper
from calculus_agent.schemas import KnowledgeQuota, PaperBlueprint


def _question(
    session,
    number: int,
    question_type: str,
    knowledge: KnowledgeNode,
    *,
    image_path: str | None = None,
    source_name: str = "test",
) -> Question:
    draft = QuestionDraft(
        source_name=source_name,
        source_item_id=str(number),
        variant=1,
        subject="初中数学",
        grade="八年级",
        question_type=question_type,
        question_text=f"第 {number} 题",
        reference_answers_json=[str(number)],
        normalized_fingerprint=str(number).zfill(64),
        status="approved",
        image_path=image_path,
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        grade=draft.grade,
        question_type=question_type,
        final_answer=str(number),
        solution_json={"solution_steps": [f"解析 {number}"]},
        verification_status="verified",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    session.add(
        QuestionKnowledgeLink(
            question_id=question.id, knowledge_node_id=knowledge.id, relation_type="primary_concept"
        )
    )
    return question


def test_compose_paper_satisfies_explicit_constraints(session):
    knowledge = KnowledgeNode(
        node_type="concept", name="一次函数", normalized_name="一次函数", review_status="approved"
    )
    session.add(knowledge)
    session.flush()
    _question(session, 1, "选择题", knowledge)
    _question(session, 2, "选择题", knowledge)
    _question(session, 3, "解答题", knowledge)
    session.flush()

    result = compose_paper(
        session,
        PaperBlueprint(
            title="八年级测试",
            grade="八年级",
            total_questions=3,
            total_score=100,
            question_type_counts={"选择题": 2, "解答题": 1},
            knowledge_quotas=[KnowledgeQuota(name="一次函数", count=2)],
        ),
    )

    assert result.feasible is True
    assert result.total_score == 100
    assert len(result.items) == 3
    assert not result.warnings


def test_compose_paper_excludes_dataset_demo_and_test_sources(session):
    knowledge = KnowledgeNode(
        node_type="concept", name="函数极限", normalized_name="函数极限", review_status="approved"
    )
    session.add(knowledge)
    session.flush()
    excluded = _question(session, 1, "选择题", knowledge, source_name="CMM-Math")
    _question(session, 2, "选择题", knowledge, source_name="built-in-demo")
    _question(session, 3, "选择题", knowledge, source_name="test_source")
    retained = _question(session, 4, "选择题", knowledge, source_name="ocr_import")
    session.flush()

    result = compose_paper(session, PaperBlueprint(
        total_questions=1,
        total_score=5,
        question_type_counts={"选择题": 1},
    ))

    assert result.feasible is True
    assert [item.question_id for item in result.items] == [retained.id]
    assert excluded.id not in [item.question_id for item in result.items]


def test_ocr_question_type_aliases_are_available_to_chinese_blueprint(session):
    knowledge = KnowledgeNode(
        node_type="concept", name="函数极限", normalized_name="函数极限", review_status="approved"
    )
    session.add(knowledge)
    session.flush()
    _question(session, 101, "calculation", knowledge)
    _question(session, 102, "proof", knowledge)
    session.flush()

    result = compose_paper(session, PaperBlueprint(
        total_questions=2,
        total_score=20,
        question_type_counts={"计算题": 1, "证明题": 1},
    ))

    assert result.feasible is True
    assert [item.question_type for item in result.items] == ["计算题", "证明题"]


def test_compose_paper_reports_infeasible_requirement(session):
    result = compose_paper(
        session,
        PaperBlueprint(total_questions=2, question_type_counts={"填空题": 2}),
    )
    assert result.feasible is False
    assert "未满足约束：题目总数" in result.warnings
    assert "未满足约束：题型：填空题" in result.warnings


def test_soft_knowledge_with_zero_links_does_not_block_candidates(session):
    unrelated = KnowledgeNode(
        node_type="concept", name="未标注", normalized_name="未标注", review_status="approved"
    )
    session.add(unrelated)
    session.flush()
    _question(session, 201, "selection", unrelated)
    session.flush()
    result = compose_paper(session, PaperBlueprint(
        total_questions=1,
        total_score=10,
        question_type_counts={"选择题": 1},
        soft_knowledge_preferences=["函数极限"],
    ))
    assert result.feasible is True
    assert len(result.items) == 1
    assert any("目标知识点“函数极限”关联不足" in warning for warning in result.warnings)


def test_hard_knowledge_shortage_still_blocks_paper(session):
    unrelated = KnowledgeNode(
        node_type="concept", name="其他", normalized_name="其他", review_status="approved"
    )
    session.add(unrelated)
    session.flush()
    _question(session, 202, "selection", unrelated)
    session.flush()
    result = compose_paper(session, PaperBlueprint(
        total_questions=1,
        total_score=10,
        question_type_counts={"选择题": 1},
        knowledge_quotas=[{"name": "函数极限", "count": 1}],
    ))
    assert result.feasible is False
    check = next(item for item in result.constraints if item.name == "知识点：函数极限")
    assert check.satisfied is False


def test_soft_knowledge_is_preferred_when_available(session):
    preferred = KnowledgeNode(
        node_type="concept", name="函数极限", normalized_name="函数极限", review_status="approved"
    )
    unrelated = KnowledgeNode(
        node_type="concept", name="其他知识", normalized_name="其他知识", review_status="approved"
    )
    session.add_all([preferred, unrelated])
    session.flush()
    preferred_question = _question(session, 203, "selection", preferred)
    _question(session, 204, "selection", unrelated)
    session.flush()
    result = compose_paper(session, PaperBlueprint(
        total_questions=1,
        total_score=10,
        question_type_counts={"选择题": 1},
        soft_knowledge_preferences=["函数极限"],
    ))
    assert result.feasible is True
    assert result.items[0].question_id == preferred_question.id


def test_fractional_scores_return_infeasible_report_instead_of_validation_error(session):
    result = compose_paper(
        session,
        PaperBlueprint(
            total_questions=4,
            total_score=70,
            sections=[
                {
                    "question_type": "解答题",
                    "count": 4,
                    "score_per_question": 17.5,
                    "total_score": 70,
                }
            ],
        ),
    )
    assert result.feasible is False
    total_score_check = next(item for item in result.constraints if item.name == "试卷总分")
    assert total_score_check.actual == 0.0
    assert total_score_check.required == 70


def test_compose_paper_does_not_fill_with_unrelated_knowledge(session):
    target = KnowledgeNode(
        node_type="concept", name="一次函数", normalized_name="一次函数", review_status="approved"
    )
    unrelated = KnowledgeNode(
        node_type="concept", name="统计", normalized_name="统计", review_status="approved"
    )
    session.add_all([target, unrelated])
    session.flush()
    _question(session, 1, "选择题", target)
    _question(session, 2, "选择题", unrelated)
    session.flush()

    result = compose_paper(
        session,
        PaperBlueprint(
            grade="八年级",
            total_questions=2,
            knowledge_quotas=[KnowledgeQuota(name="一次函数", count=1)],
            strict_knowledge=True,
        ),
    )

    assert len(result.items) == 1
    assert result.feasible is False
    assert all("一次函数" in item.knowledge for item in result.items)


def test_compose_paper_checks_image_requirement(session):
    knowledge = KnowledgeNode(
        node_type="concept", name="几何", normalized_name="几何", review_status="approved"
    )
    session.add(knowledge)
    session.flush()
    _question(session, 1, "解答题", knowledge, image_path="figure.png")
    session.flush()

    result = compose_paper(
        session,
        PaperBlueprint(
            grade="八年级",
            total_questions=1,
            knowledge_quotas=[KnowledgeQuota(name="几何", count=1)],
            image_question_count=1,
        ),
    )

    assert result.feasible is True
    assert result.items[0].has_image is True


def test_compose_paper_keeps_locked_questions_and_excludes_replaced_questions(session):
    knowledge = KnowledgeNode(
        node_type="concept", name="一次函数", normalized_name="一次函数", review_status="approved"
    )
    session.add(knowledge)
    session.flush()
    locked = _question(session, 1, "选择题", knowledge)
    excluded = _question(session, 2, "选择题", knowledge)
    replacement = _question(session, 3, "选择题", knowledge)
    session.flush()

    result = compose_paper(
        session,
        PaperBlueprint(
            grade="八年级",
            total_questions=2,
            locked_question_ids=[locked.id],
            excluded_question_ids=[excluded.id],
        ),
    )

    ids = [item.question_id for item in result.items]
    assert ids[0] == locked.id
    assert replacement.id in ids
    assert excluded.id not in ids
    assert result.feasible is True


def test_compose_paper_reports_missing_locked_question(session):
    result = compose_paper(
        session,
        PaperBlueprint(total_questions=1, locked_question_ids=["missing-question"]),
    )

    assert result.feasible is False
    assert "未满足约束：指定题目" in result.warnings


def test_compose_paper_applies_manual_order_and_score_override(session):
    knowledge = KnowledgeNode(
        node_type="concept", name="一次函数", normalized_name="一次函数", review_status="approved"
    )
    session.add(knowledge)
    session.flush()
    first = _question(session, 1, "选择题", knowledge)
    second = _question(session, 2, "解答题", knowledge)
    third = _question(session, 3, "填空题", knowledge)
    session.flush()

    result = compose_paper(
        session,
        PaperBlueprint(
            grade="八年级",
            total_questions=3,
            total_score=100,
            manual_question_ids=[first.id],
            question_order=[third.id, first.id, second.id],
            score_overrides={first.id: 20},
        ),
    )

    assert [item.question_id for item in result.items] == [third.id, first.id, second.id]
    assert [item.score for item in result.items] == [40, 20, 40]
    assert result.feasible is True

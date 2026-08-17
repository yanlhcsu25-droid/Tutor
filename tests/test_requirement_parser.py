from calculus_agent.requirements.parser import (
    _normalize_blueprint_payload,
    apply_blueprint_modification,
    apply_explicit_constraints,
)
from calculus_agent.schemas import PaperBlueprint, SectionRequirement


def test_blueprint_preserves_total_questions():
    blueprint = PaperBlueprint(
        total_questions=8,
    )
    result = apply_explicit_constraints("生成一元函数微分学测试卷", blueprint)
    assert result.total_questions == 8


def test_explicit_knowledge_and_image_constraints_override_model():
    blueprint = PaperBlueprint(
        total_questions=10,
        knowledge_quotas=[{"name": "极限运算", "count": 5}],
    )
    result = apply_explicit_constraints(
        "极限运算至少5题，需要含图片", blueprint
    )
    assert [(item.name, item.count) for item in result.knowledge_quotas] == [
        ("极限运算", 5)
    ]
    assert result.image_question_count == 1


def test_explicit_section_scores_are_preserved_in_blueprint():
    blueprint = PaperBlueprint(total_questions=1, total_score=1)
    result = apply_explicit_constraints(
        "选择题4道，每题5分；填空题2道，每题5分；计算题4道，共70分。",
        blueprint,
    )
    assert result.total_questions == 10
    assert result.total_score == 100
    assert [
        (item.question_type, item.count, item.score_per_question, item.total_score)
        for item in result.sections
    ] == [
        ("选择题", 4, 5, 20),
        ("填空题", 2, 5, 10),
        ("计算题", 4, 17.5, 70),
    ]


def test_fill_in_template_formula_is_parsed_without_score_inference():
    blueprint = PaperBlueprint(total_questions=1, total_score=1)
    result = apply_explicit_constraints(
        """帮我生成一套【函数与极限】章节练习。
题目总数：10 题
题目总分：100 分
选择题 2 道 × 5 分 = 10 分
填空题 1 道 × 5 分 = 5 分
计算题 5 道 × 13 分 = 65 分
证明题 2 道 × 10 分 = 20 分""",
        blueprint,
    )

    assert result.total_questions == 10
    assert result.total_score == 100
    assert [
        (item.question_type, item.count, item.score_per_question, item.total_score)
        for item in result.sections
    ] == [
        ("选择题", 2, 5, 10),
        ("填空题", 1, 5, 5),
        ("计算题", 5, 13, 65),
        ("证明题", 2, 10, 20),
    ]


def _existing_blueprint() -> PaperBlueprint:
    return PaperBlueprint(
        total_questions=10,
        total_score=70,
        sections=[
            SectionRequirement(question_type="选择题", count=3, score_per_question=5, total_score=15),
            SectionRequirement(question_type="填空题", count=3, score_per_question=5, total_score=15),
            SectionRequirement(question_type="计算题", count=4, score_per_question=10, total_score=40),
        ],
    )


def test_add_three_proof_questions_preserves_existing_sections():
    result = apply_blueprint_modification("加入3道证明题", _existing_blueprint())
    assert result.question_type_counts == {
        "选择题": 3, "填空题": 3, "计算题": 4, "证明题": 3,
    }
    assert result.total_questions == 13
    assert result.total_questions == sum(section.count for section in result.sections)
    assert result.total_score == sum(section.total_score for section in result.sections)


def test_add_proof_questions_accepts_type_before_count_wording():
    result = apply_blueprint_modification("加入证明题2道", _existing_blueprint())

    assert result.question_type_counts == {
        "选择题": 3, "填空题": 3, "计算题": 4, "证明题": 2,
    }
    assert result.total_questions == 12


def test_prefixed_conversation_accepts_type_before_count_wording():
    result = apply_blueprint_modification(
        "修改要求：高等数学测试卷的方案中加入证明题2道",
        _existing_blueprint(),
    )

    assert result.question_type_counts["证明题"] == 2


def test_new_proof_type_does_not_inherit_fractional_calculation_score():
    base = PaperBlueprint(
        total_questions=6,
        total_score=100,
        sections=[
            SectionRequirement(question_type="选择题", count=2, score_per_question=5, total_score=10),
            SectionRequirement(question_type="填空题", count=1, score_per_question=5, total_score=5),
            SectionRequirement(
                question_type="计算题",
                count=3,
                score_per_question=28.333333333333332,
                total_score=85,
            ),
        ],
    )

    result = apply_blueprint_modification(
        "修改要求：高等数学测试卷的方案中加入证明题2道",
        base,
    )

    proof = next(item for item in result.sections if item.question_type == "证明题")
    assert result.total_questions == 8
    assert proof.score_per_question == 10
    assert proof.total_score == 20
    assert result.total_score == 120


def test_reduce_two_proof_questions():
    added = apply_blueprint_modification("加入3道证明题", _existing_blueprint())
    result = apply_blueprint_modification("减少2道证明题", added)
    assert result.question_type_counts["证明题"] == 1
    assert result.total_questions == 11


def test_replace_one_qa_question_with_one_proof_question():
    result = apply_blueprint_modification(
        "把1道计算题换成1道证明题", _existing_blueprint()
    )
    assert result.question_type_counts == {
        "选择题": 3, "填空题": 3, "计算题": 3, "证明题": 1,
    }
    assert result.total_questions == 10
    assert result.total_score == 70


def test_proof_alias_and_section_n_are_normalized_before_summary():
    result = PaperBlueprint.model_validate({
        "total_questions": 0,
        "total_score": 1,
        "sections": [{
            "question_type": "proof", "n": 3,
            "score_per_question": 10, "total_score": 30,
        }],
    })
    assert result.sections[0].question_type == "证明题"
    assert result.sections[0].count == 3
    assert result.total_questions == 3
    assert result.total_score == 30


def test_keep_100_points_when_adding_three_proof_questions():
    base = PaperBlueprint(
        total_questions=10,
        total_score=100,
        sections=[
            SectionRequirement(question_type="选择题", count=3, score_per_question=4, total_score=12),
            SectionRequirement(question_type="填空题", count=3, score_per_question=6, total_score=18),
            SectionRequirement(question_type="计算题", count=4, score_per_question=17.5, total_score=70),
        ],
    )
    result = apply_blueprint_modification("保持100分，加入3道证明题", base)
    by_type = {section.question_type: section for section in result.sections}
    assert (by_type["选择题"].count, by_type["选择题"].score_per_question) == (3, 4)
    assert (by_type["填空题"].count, by_type["填空题"].score_per_question) == (3, 6)
    assert (by_type["计算题"].count, by_type["计算题"].score_per_question) == (4, 10)
    assert (by_type["证明题"].count, by_type["证明题"].score_per_question) == (3, 10)
    assert result.total_questions == sum(section.count for section in result.sections) == 13
    assert sum(section.total_score for section in result.sections) == result.total_score == 100


def test_focus_topics_become_soft_preferences_and_default_total_remains_100():
    model_blueprint = PaperBlueprint(
        total_questions=10,
        total_score=100,
        sections=[
            SectionRequirement(question_type="选择题", count=3, score_per_question=4, total_score=12),
            SectionRequirement(question_type="填空题", count=2, score_per_question=5, total_score=10),
            SectionRequirement(question_type="计算题", count=3, score_per_question=14, total_score=42),
            SectionRequirement(question_type="证明题", count=2, score_per_question=18, total_score=36),
        ],
        knowledge_quotas=[
            {"name": "函数极限", "count": 4},
            {"name": "极限运算法则", "count": 3},
            {"name": "无穷小", "count": 3},
        ],
        strict_knowledge=True,
    )
    result = apply_explicit_constraints(
        "重点覆盖函数极限、极限运算法则、无穷小", model_blueprint
    )
    assert result.knowledge_quotas == []
    assert result.soft_knowledge_preferences == ["函数的极限", "极限运算法则", "无穷小与无穷大"]
    assert result.strict_knowledge is False
    assert result.total_questions == 10
    assert result.total_score == sum(section.total_score for section in result.sections) == 100
    assert result.question_type_counts == model_blueprint.question_type_counts


def test_explicit_knowledge_count_remains_hard_quota():
    blueprint = PaperBlueprint(total_questions=5, total_score=100)
    result = apply_explicit_constraints("函数极限必须4题", blueprint)
    assert [(item.name, item.count) for item in result.knowledge_quotas] == [("函数极限", 4)]
    assert result.soft_knowledge_preferences == []


def test_at_least_count_before_topic_remains_hard_quota():
    blueprint = PaperBlueprint(total_questions=5, total_score=100)
    result = apply_explicit_constraints("至少3道无穷小", blueprint)
    assert [(item.name, item.count) for item in result.knowledge_quotas] == [("无穷小", 3)]


def test_topic_with_explicit_question_count_remains_hard_quota():
    blueprint = PaperBlueprint(total_questions=5, total_score=100)
    result = apply_explicit_constraints("函数极限出4题", blueprint)
    assert [(item.name, item.count) for item in result.knowledge_quotas] == [("函数极限", 4)]


def test_string_soft_knowledge_preferences_are_repaired_before_validation():
    payload = _normalize_blueprint_payload({
        "total_questions": 10,
        "soft_knowledge_preferences": "重点覆盖函数极限、极限运算法则、无穷小",
    })

    blueprint = PaperBlueprint.model_validate(payload)

    assert blueprint.soft_knowledge_preferences == [
        "函数的极限",
        "极限运算法则",
        "无穷小与无穷大",
    ]


def test_focus_rule_keeps_normalized_model_soft_preferences_without_quotas():
    blueprint = PaperBlueprint.model_validate(_normalize_blueprint_payload({
        "total_questions": 10,
        "soft_knowledge_preferences": "函数极限、极限运算法则、无穷小",
    }))

    result = apply_explicit_constraints(
        "重点覆盖函数极限、极限运算法则、无穷小",
        blueprint,
    )

    assert result.soft_knowledge_preferences == [
        "函数的极限",
        "极限运算法则",
        "无穷小与无穷大",
    ]

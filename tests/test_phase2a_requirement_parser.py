from calculus_agent.agent import parse_teacher_requirement


def test_phase2a_examples():
    cases = [
        ("帮我出一套第一章测试卷", "chapter_test", ["第一章"], 100, "normal", False),
        ("帮我出一套第一章 100 分的普通章节测试", "chapter_test", ["第一章"], 100, "normal", False),
        ("给第一节出一套课后练习", "homework", ["第一节"], None, "normal", False),
        ("帮我出一套期中考试", "midterm", [], 100, "normal", True),
        ("帮我出一套期末考试", "final", [], 100, "normal", True),
        ("第一章测试，简单一点，多一点计算题", "chapter_test", ["第一章"], 100, "easy", False),
    ]
    for text, paper_type, scope, score, difficulty, clarification in cases:
        result = parse_teacher_requirement(text)
        assert result.paper_type == paper_type
        assert result.scope == scope
        assert result.total_score == score
        assert result.difficulty == difficulty
        assert result.need_clarification is clarification
    assert parse_teacher_requirement(cases[-1][0]).preferences.more_question_types == ["计算题"]


def test_clarification_questions_are_actionable():
    assert "范围" in parse_teacher_requirement("帮我出一套期中考试").clarification_questions[0]
    assert "难度占比" in parse_teacher_requirement("帮我出一套期末考试").clarification_questions[0]

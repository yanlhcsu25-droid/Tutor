from calculus_agent.api import _conversation_summary_title
from calculus_agent.models import TeacherAgentConversationMessage


def _message(content: str) -> TeacherAgentConversationMessage:
    return TeacherAgentConversationMessage(conversation_id="c", role="user", content=content)


def test_paper_conversation_uses_chapter_test_title():
    assert _conversation_summary_title(
        [_message("生成函数与极限测试")], has_paper=False, has_teaching_design=False,
    ) == "函数与极限章节测试"


def test_practice_intent_wins_over_generated_paper():
    assert _conversation_summary_title(
        [_message("学生洛必达不会，生成练习")], has_paper=True, has_teaching_design=False,
    ) == "洛必达法则专项训练"


def test_teaching_consultation_uses_teaching_method_title():
    assert _conversation_summary_title(
        [_message("极限怎么讲")], has_paper=False, has_teaching_design=False,
    ) == "函数与极限教学方法"

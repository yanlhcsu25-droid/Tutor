import json

from calculus_agent.requirements.conversation_agent import (
    PaperConversationAgent,
    apply_paper_patch,
)
from calculus_agent.schemas import PaperBlueprint, PaperItemRead, PaperPreviewRead, SectionRequirement


def _base() -> PaperBlueprint:
    return PaperBlueprint(
        total_questions=10,
        total_score=100,
        sections=[
            SectionRequirement(question_type="选择题", count=2, score_per_question=5, total_score=10),
            SectionRequirement(question_type="填空题", count=1, score_per_question=5, total_score=5),
            SectionRequirement(question_type="计算题", count=5, score_per_question=13, total_score=65),
            SectionRequirement(question_type="证明题", count=2, score_per_question=10, total_score=20),
        ],
    )


class FakeBackend:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.messages = []

    def complete(self, messages, tools):
        self.messages = messages
        assert tools == []
        return {"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}


def test_agent_can_apply_full_restatement_without_increment_words():
    backend = FakeBackend({
        "action": "update",
        "message": "已按完整方案调整。",
        "operations": [{
            "action": "set_sections",
            "sections": [
                {"question_type": "选择题", "count": 2, "score_per_question": 5, "total_score": 10},
                {"question_type": "填空题", "count": 1, "score_per_question": 5, "total_score": 5},
                {"question_type": "计算题", "count": 4, "score_per_question": 15, "total_score": 60},
                {"question_type": "证明题", "count": 3, "score_per_question": 10, "total_score": 30},
            ],
        }],
    })

    action, message, result = PaperConversationAgent(backend).decide(
        "选择题2题，填空题1题，计算题4题，证明题3题",
        _base(),
    )

    assert action == "update"
    assert message == "已按完整方案调整。"
    assert result is not None
    assert result.question_type_counts == {
        "选择题": 2, "填空题": 1, "计算题": 4, "证明题": 3,
    }
    assert '"blueprint"' in backend.messages[-1]["content"]


def test_agent_returns_clarification_instead_of_invalid_patch():
    backend = FakeBackend({
        "action": "clarify",
        "message": "你希望减少计算题，还是只修改每题分值？",
    })

    action, message, result = PaperConversationAgent(backend).decide(
        "计算题还是不太合适",
        _base(),
    )

    assert action == "clarify"
    assert "减少计算题" in message
    assert result is None


def test_patch_can_lock_replace_and_rescore_by_current_slot():
    paper = PaperPreviewRead(
        title="测试卷",
        total_score=10,
        feasible=True,
        constraints=[],
        items=[
            PaperItemRead(
                question_id="q1", question_text="第一题", question_type="选择题", score=5
            ),
            PaperItemRead(
                question_id="q2", question_text="第二题", question_type="选择题", score=5
            ),
        ],
    )

    result = apply_paper_patch(
        _base(),
        [
            {"action": "lock_question", "slot": 1},
            {"action": "replace_question", "slot": 2},
            {"action": "update_score", "slot": 1, "score": 8},
        ],
        paper=paper,
    )

    assert result.locked_question_ids == ["q1"]
    assert result.excluded_question_ids == ["q2"]
    assert result.score_overrides == {"q1": 8}

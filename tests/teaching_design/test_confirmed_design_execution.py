from calculus_agent.application.teaching_design_generation import (
    TeachingDesignPaperGenerationService,
)
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.teaching_design.service import TeachingDesignService


def _confirmed(
    session,
    *,
    conversation_id: str,
    content: dict,
):
    service = TeachingDesignService(session)
    design = service.create(
        owner_key="local_teacher",
        conversation_id=conversation_id,
        content=content,
        run_id="run-create",
        source_user_message="创建教学设计",
    )
    return service.confirm(
        design.version_id,
        conversation_id=conversation_id,
        run_id="run-confirm",
    )


def test_execution_blocks_only_truly_unsupported_design_before_generation_state(
    session,
):
    conversation_id = "unsupported-design"
    store = DatabasePendingReplacementStore(session)
    design = _confirmed(
        session,
        conversation_id=conversation_id,
        content={
            "title": "未知能力维度",
            "objective": "完成章节复习与测评。",
            "scope_names": ["第一章"],
            "assessment_plan": {
                "paper_type": "chapter_test",
                "total_score": 100,
                "difficulty": "normal",
                "ability_weights": {
                    "memorization": 100,
                },
            },
        },
    )

    result = TeachingDesignPaperGenerationService(
        session=session,
        store=store,
        conversation_id=conversation_id,
    ).execute(design)

    assert result.ok is False
    assert result.code == "teaching_design_not_executable"
    assert result.requires_design_revision is True
    assert result.unsupported_design_constraints == [
        "ability_weight:memorization"
    ]
    assert store.get_generation(conversation_id) is None

def test_execution_rejects_unrelated_pending_generation(session):
    conversation_id = "pending-collision"
    store = DatabasePendingReplacementStore(session)
    design = _confirmed(
        session,
        conversation_id=conversation_id,
        content={
            "title": "第一章测试",
            "objective": "完成第一章测评。",
            "scope_names": ["第一章"],
        },
    )

    # A legacy pending plan is a different source of truth and must never be
    # silently merged with the confirmed TeachingDesign execution.
    from calculus_agent.agent.conversation_state import PendingGeneration
    from calculus_agent.agent.schemas import GeneratePaperInput

    store.set_generation(
        conversation_id,
        PendingGeneration(
            request=GeneratePaperInput(
                paper_type="chapter_test",
                scope_names=["第二章"],
            )
        ),
    )

    result = TeachingDesignPaperGenerationService(
        session=session,
        store=store,
        conversation_id=conversation_id,
    ).execute(design)

    assert result.ok is False
    assert result.code == "pending_generation_exists"
    assert store.get_generation(conversation_id) is not None


def test_execution_immediately_previews_and_confirms_without_second_teacher_confirmation(
    session,
    monkeypatch,
):
    conversation_id = "immediate-execution"
    design = _confirmed(
        session,
        conversation_id=conversation_id,
        content={
            "title": "第一章测试",
            "objective": "完成第一章核心内容测评。",
            "scope_names": ["第一章"],
            "assessment_plan": {
                "paper_type": "chapter_test",
                "total_score": 100,
                "difficulty": "normal",
            },
        },
    )

    calls = []

    from calculus_agent.agent.schemas import (
        GeneratePaperInput,
        GenerationPlanPreview,
    )
    from calculus_agent.agent.tools.paper_tools import (
        GeneratePaperToolResult,
        PaperSummary,
    )
    import calculus_agent.application.teaching_design_generation as module

    class FakeGenerationService:
        def __init__(
            self,
            *,
            session,
            store,
            conversation_id,
            teaching_design_version_id,
        ):
            calls.append(
                (
                    "init",
                    conversation_id,
                    teaching_design_version_id,
                )
            )
            self.teaching_design_version_id = (
                teaching_design_version_id
            )

        def preview(self, patch):
            calls.append(("preview", patch.model_dump(exclude_unset=True)))
            return GenerationPlanPreview(
                ok=True,
                request=GeneratePaperInput(
                    paper_type="chapter_test",
                    scope_names=["第一章"],
                    total_score=100,
                    difficulty_level="normal",
                ),
                title="第一章测试卷",
                total_questions=10,
                total_score=100,
            )

        def confirm(self):
            calls.append(("confirm", self.teaching_design_version_id))
            return GeneratePaperToolResult(
                ok=True,
                paper_id="paper-1",
                version_id="paper-1",
                summary=PaperSummary(
                    total_questions=10,
                    total_score=100,
                    question_type_counts={},
                ),
                validation_status="passed",
            )

    monkeypatch.setattr(
        module,
        "GenerationService",
        FakeGenerationService,
    )

    store = DatabasePendingReplacementStore(session)
    result = TeachingDesignPaperGenerationService(
        session=session,
        store=store,
        conversation_id=conversation_id,
    ).execute(design)

    assert result.ok is True
    assert result.paper is not None
    assert result.paper.paper_id == "paper-1"
    assert [item[0] for item in calls] == [
        "init",
        "preview",
        "confirm",
    ]
    assert calls[0][2] == design.version_id
    assert calls[2][1] == design.version_id

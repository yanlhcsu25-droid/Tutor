from datetime import UTC, datetime

from calculus_agent.application.teaching_design_generation import (
    TeachingDesignPaperGenerationService,
)
from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
)
from calculus_agent.agent.schemas import (
    GeneratePaperInput,
    GenerationPlanPreview,
)
from calculus_agent.agent.tools.paper_tools import GeneratePaperToolResult
from calculus_agent.schemas import (
    ConstraintViolationRead,
    ValidationReportRead,
)
from calculus_agent.teaching_design.service import TeachingDesignService


def _confirmed(session, conversation_id):
    service = TeachingDesignService(session)
    design = service.create(
        owner_key="local_teacher",
        conversation_id=conversation_id,
        content={
            "title": "执行失败分类边界",
            "objective": "验证执行失败不会被提前误判成教学设计必须修改。",
            "scope_names": ["第一章"],
        },
        run_id="run-create",
        source_user_message="创建测试设计",
    )
    return service.confirm(
        design.version_id,
        conversation_id=conversation_id,
        run_id="run-confirm",
    )


def test_execution_failure_is_not_automatically_classified_as_design_revision(
    session,
    monkeypatch,
):
    conversation_id = "t4-execution-classification"
    design = _confirmed(session, conversation_id)

    import calculus_agent.application.teaching_design_generation as module

    class FakeGenerationService:
        def __init__(self, **_kwargs):
            pass

        def preview(self, _patch):
            return GenerationPlanPreview(
                ok=True,
                request=GeneratePaperInput(
                    paper_type="chapter_test",
                    scope_names=["第一章"],
                    total_score=100,
                    difficulty_level="normal",
                ),
                title="测试卷",
                total_questions=10,
                total_score=100,
            )

        def confirm(self):
            return GeneratePaperToolResult(
                ok=False,
                paper_id="failed-paper",
                version_id="failed-paper",
                blocking_errors=["paper_validation_failed"],
                validation_status="failed",
                validation_report=ValidationReportRead(
                    id="report-1",
                    paper_id="failed-paper",
                    passed=False,
                    violations=[
                        ConstraintViolationRead(
                            code="SOLUTION_MISSING",
                            field="solution",
                            required=0,
                            actual=1,
                            repairable=True,
                            message="题目缺少解析",
                        )
                    ],
                    created_at=datetime.now(UTC),
                ),
            )

    monkeypatch.setattr(
        module,
        "GenerationService",
        FakeGenerationService,
    )

    result = TeachingDesignPaperGenerationService(
        session=session,
        store=DatabasePendingReplacementStore(session),
        conversation_id=conversation_id,
    ).execute(design)

    assert result.ok is False
    assert result.code == "teaching_design_generation_failed"
    assert result.requires_design_revision is False
    assert result.paper is not None
    assert result.paper.validation_status == "failed"

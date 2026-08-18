from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
    PendingGeneration,
)
from calculus_agent.agent.schemas import GeneratePaperInput
from calculus_agent.agent.services.generation import GenerationService
from calculus_agent.agent.tools.paper_tools import GeneratePaperToolResult, PaperSummary
import calculus_agent.agent.services.generation as generation_module
from calculus_agent.models import Paper, PaperBlueprintRecord
from calculus_agent.teaching_design.service import TeachingDesignService


def test_generation_service_materializes_exact_teaching_design_version_on_paper(
    session,
    monkeypatch,
):
    conversation_id = "paper-provenance"
    design_service = TeachingDesignService(session)
    design = design_service.create(
        owner_key="local_teacher",
        conversation_id=conversation_id,
        content={
            "title": "第一章测试",
            "objective": "完成第一章测评。",
            "scope_names": ["第一章"],
        },
        run_id="run-create",
        source_user_message="设计第一章测试",
    )
    design = design_service.confirm(
        design.version_id,
        conversation_id=conversation_id,
        run_id="run-confirm",
    )

    blueprint = PaperBlueprintRecord(
        title="第一章测试卷",
        blueprint_json={
            "title": "第一章测试卷",
            "total_questions": 1,
            "total_score": 10,
            "question_type_counts": {"计算题": 1},
        },
        status="used",
    )
    session.add(blueprint)
    session.flush()

    def fake_generate_paper_from_input(session, request):
        paper = Paper(
            blueprint_id=blueprint.id,
            version=1,
            status="passed",
            title="第一章测试卷",
            total_score=10,
            validation_status="passed",
        )
        session.add(paper)
        session.flush()
        paper.root_paper_id = paper.id
        session.flush()
        return GeneratePaperToolResult(
            ok=True,
            paper_id=paper.id,
            version_id=paper.id,
            summary=PaperSummary(
                total_questions=1,
                total_score=10,
                question_type_counts={"计算题": 1},
            ),
            validation_status="passed",
        )

    monkeypatch.setattr(
        generation_module,
        "generate_paper_from_input",
        fake_generate_paper_from_input,
    )

    store = DatabasePendingReplacementStore(session)
    store.set_generation(
        conversation_id,
        PendingGeneration(
            request=GeneratePaperInput(
                paper_type="chapter_test",
                scope_names=["第一章"],
                total_score=10,
            ),
            teaching_design_version_id=design.version_id,
        ),
    )

    result = GenerationService(
        session=session,
        store=store,
        conversation_id=conversation_id,
        teaching_design_version_id=design.version_id,
    ).confirm()

    paper = session.get(Paper, str(result.paper_id))
    assert paper is not None
    assert paper.teaching_design_version_id == design.version_id

    memory = store.get_memory(conversation_id)
    assert (
        memory.last_completed_paper["teaching_design_version_id"]
        == design.version_id
    )

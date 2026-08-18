from calculus_agent.models import Paper, PaperBlueprintRecord
from calculus_agent.papers.workflow import _new_version
from calculus_agent.teaching_design.service import TeachingDesignService


def _confirmed_design(session):
    service = TeachingDesignService(session)
    design = service.create(
        owner_key="local-teacher",
        conversation_id="conv-1",
        content={
            "title": "第一章复习",
            "objective": "复习第一章核心知识。",
            "scope_names": ["第一章"],
        },
        run_id="run-create",
        source_user_message="设计第一章复习。",
    )
    return service.confirm(
        design.version_id,
        conversation_id="conv-1",
        run_id="run-confirm",
    )


def test_paper_versions_keep_exact_teaching_design_version_reference(session):
    design = _confirmed_design(session)

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

    paper_v1 = Paper(
        blueprint_id=blueprint.id,
        version=1,
        status="draft",
        title="第一章测试卷",
        total_score=10,
        teaching_design_version_id=design.version_id,
        validation_status="pending",
    )
    session.add(paper_v1)
    session.flush()
    paper_v1.root_paper_id = paper_v1.id
    session.flush()

    paper_v2 = _new_version(session, paper_v1)

    assert paper_v2.parent_version_id == paper_v1.id
    assert paper_v2.teaching_design_version_id == design.version_id

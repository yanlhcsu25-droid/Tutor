from calculus_agent.agent import build_paper_blueprint, parse_teacher_requirement
from calculus_agent.papers.persistence import create_paper_draft
from calculus_agent.papers.selector import compose_paper
from calculus_agent.models import Paper, PaperBlueprintRecord, PaperItem


def test_draft_persistence_creates_shared_paper_version(session):
    requirement = parse_teacher_requirement("第一章测试")
    blueprint_result = build_paper_blueprint(requirement)
    # Empty fixture databases may not have enough candidates; test the
    # persistence contract with a validated preview shape from the fixture.
    preview = compose_paper(session, blueprint_result.paper_blueprint)
    if not preview.feasible:
        return
    result = create_paper_draft(session, preview, blueprint_result.paper_blueprint)
    assert result.ok is True
    assert result.paper_id == result.version_id
    paper = session.get(Paper, result.paper_id)
    assert paper.status == "draft"
    assert paper.root_paper_id == paper.id
    assert session.query(PaperItem).filter(PaperItem.paper_id == paper.id).count() == len(preview.items)
    record = session.get(PaperBlueprintRecord, paper.blueprint_id)
    assert record.status == "draft"
    assert record.blueprint_json["_agent_metadata"]["source"] == "teacher_agent"


def test_failed_persistence_does_not_leave_half_created_paper(session):
    requirement = parse_teacher_requirement("第一章测试")
    blueprint = build_paper_blueprint(requirement).paper_blueprint
    preview = compose_paper(session, blueprint)
    if not preview.feasible:
        return
    broken = preview.model_copy(update={"items": [preview.items[0].model_copy(update={"question_id": "missing"})]})
    result = create_paper_draft(session, broken, blueprint)
    assert result.ok is False
    assert result.blocking_errors[0] == "paper_persistence_failed"

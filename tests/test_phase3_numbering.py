import pytest
from calculus_agent.models import Paper, PaperBlueprintRecord, PaperItem, Question, QuestionDraft
from calculus_agent.papers.workflow import BlueprintStateError, reorder_paper_items


def _question(session, sid, qtype):
    draft = QuestionDraft(
        source_name="reorder-v2", source_item_id=sid, variant=1,
        subject="高等数学", question_type=qtype, question_text=sid,
        normalized_fingerprint=f"fp-{sid}", status="approved",
    )
    session.add(draft)
    session.flush()
    question = Question(
        draft_id=draft.id, question_text=sid, question_type=qtype,
        verification_status="manual_verified", review_status="approved", is_active=True,
    )
    session.add(question)
    session.flush()
    return question


def test_backend_reorder_rejects_cross_section(session):
    blueprint = PaperBlueprintRecord(title="reorder-v2", blueprint_json={}, status="used")
    session.add(blueprint)
    session.flush()
    paper = Paper(
        blueprint_id=blueprint.id, version=1, status="draft",
        title="reorder-v2", total_score=20, validation_status="pending",
    )
    session.add(paper)
    session.flush()
    paper.root_paper_id = paper.id

    specs = [("s1", "选择题"), ("s2", "选择题"), ("f1", "填空题"), ("f2", "填空题")]
    rows = []
    for position, (sid, qtype) in enumerate(specs, 1):
        q = _question(session, sid, qtype)
        row = PaperItem(
            paper_id=paper.id, question_id=q.id, section=qtype,
            position=position, score=5, locked=False,
        )
        session.add(row)
        rows.append(row)
    session.flush()

    with pytest.raises(BlueprintStateError, match="不能跨题型"):
        reorder_paper_items(
            session, paper.id,
            [rows[0].id, rows[2].id, rows[1].id, rows[3].id],
        )

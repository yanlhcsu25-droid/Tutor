from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
    PendingGeneration,
)
from calculus_agent.agent.generation_history import historical_question_ids
from calculus_agent.agent.schemas import GeneratePaperInput
from calculus_agent.agent.services.generation import GenerationService
from calculus_agent.agent.tools.paper_tools import GeneratePaperToolResult, PaperSummary
from calculus_agent.models import (
    ConversationGenerationRecord,
    Paper,
    PaperBlueprintRecord,
    PaperItem,
)
import calculus_agent.agent.services.generation as generation_module


def _seed_pending(session, conversation_id, *, teaching_design_version_id=None):
    store = DatabasePendingReplacementStore(session)
    store.set_generation(
        conversation_id,
        PendingGeneration(
            request=GeneratePaperInput(
                paper_type="chapter_test",
                scope_names=["第一章"],
            ),
            teaching_design_version_id=teaching_design_version_id,
        ),
    )
    return store


def _fake_generator(session, *, selected_question_id, observed_exclusions):
    def generate(_session, _request, *, excluded_question_ids=None):
        observed_exclusions.append(list(excluded_question_ids or []))
        blueprint = PaperBlueprintRecord(
            title="测试卷",
            blueprint_json={},
            status="confirmed",
        )
        session.add(blueprint)
        session.flush()
        paper = Paper(
            blueprint_id=blueprint.id,
            version=1,
            status="passed",
            title="测试卷",
            total_score=10,
            validation_status="passed",
        )
        session.add(paper)
        session.flush()
        paper.root_paper_id = paper.id
        session.add(PaperItem(
            paper_id=paper.id,
            question_id=selected_question_id,
            section="计算题",
            position=1,
            score=10,
        ))
        session.flush()
        return GeneratePaperToolResult(
            ok=True,
            paper_id=paper.id,
            version_id=paper.id,
            summary=PaperSummary(total_questions=1, total_score=10),
            validation_status="passed",
        )

    return generate


def test_successful_generation_records_conversation_question_ids(session, monkeypatch):
    conversation_id = "generation-history-a"
    store = _seed_pending(session, conversation_id)
    observed = []
    monkeypatch.setattr(
        generation_module,
        "generate_paper_from_input",
        _fake_generator(
            session,
            selected_question_id="question-a",
            observed_exclusions=observed,
        ),
    )

    result = GenerationService(
        session=session,
        store=store,
        conversation_id=conversation_id,
    ).confirm()

    assert result.ok is True
    records = list(session.query(ConversationGenerationRecord).all())
    assert len(records) == 1
    assert records[0].conversation_id == conversation_id
    assert records[0].paper_id == result.paper_id
    assert records[0].question_ids_json == ["question-a"]
    assert historical_question_ids(session, conversation_id=conversation_id) == ["question-a"]
    assert observed == [[]]


def test_next_generation_in_same_conversation_excludes_history(session, monkeypatch):
    conversation_id = "generation-history-repeat"
    store = _seed_pending(session, conversation_id)
    first_observed = []
    monkeypatch.setattr(
        generation_module,
        "generate_paper_from_input",
        _fake_generator(
            session,
            selected_question_id="question-a",
            observed_exclusions=first_observed,
        ),
    )
    GenerationService(session=session, store=store, conversation_id=conversation_id).confirm()

    _seed_pending(session, conversation_id)
    second_observed = []
    monkeypatch.setattr(
        generation_module,
        "generate_paper_from_input",
        _fake_generator(
            session,
            selected_question_id="question-b",
            observed_exclusions=second_observed,
        ),
    )
    GenerationService(session=session, store=store, conversation_id=conversation_id).confirm()

    assert first_observed == [[]]
    assert second_observed == [["question-a"]]
    assert historical_question_ids(session, conversation_id=conversation_id) == [
        "question-a",
        "question-b",
    ]


def test_other_conversation_does_not_exclude_prior_questions(session, monkeypatch):
    first_store = _seed_pending(session, "generation-history-first")
    monkeypatch.setattr(
        generation_module,
        "generate_paper_from_input",
        _fake_generator(session, selected_question_id="question-a", observed_exclusions=[]),
    )
    GenerationService(
        session=session,
        store=first_store,
        conversation_id="generation-history-first",
    ).confirm()

    second_store = _seed_pending(session, "generation-history-second")
    observed = []
    monkeypatch.setattr(
        generation_module,
        "generate_paper_from_input",
        _fake_generator(session, selected_question_id="question-a", observed_exclusions=observed),
    )
    GenerationService(
        session=session,
        store=second_store,
        conversation_id="generation-history-second",
    ).confirm()

    assert observed == [[]]


def test_teaching_design_generation_history_keeps_design_version(session, monkeypatch):
    conversation_id = "generation-history-design"
    store = _seed_pending(
        session,
        conversation_id,
        teaching_design_version_id="teaching-design-version",
    )
    monkeypatch.setattr(
        generation_module,
        "generate_paper_from_input",
        _fake_generator(session, selected_question_id="question-a", observed_exclusions=[]),
    )

    GenerationService(session=session, store=store, conversation_id=conversation_id).confirm()

    record = session.query(ConversationGenerationRecord).one()
    assert record.teaching_design_version_id == "teaching-design-version"

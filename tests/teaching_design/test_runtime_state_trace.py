from calculus_agent.agent.state_snapshot import build_runtime_state_snapshot
from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.teaching_design.service import TeachingDesignService


def test_runtime_state_snapshot_separates_working_memory_and_business_design(session):
    conversation_id = "conv-state"
    store = DatabasePendingReplacementStore(session)
    service = TeachingDesignService(session)

    design = service.create(
        owner_key="local_teacher",
        conversation_id=conversation_id,
        content={
            "title": "第一章复习",
            "objective": "复习第一章。",
            "scope_names": ["第一章"],
        },
        run_id="run-create",
        source_user_message="设计第一章复习。",
    )

    snapshot = build_runtime_state_snapshot(
        session,
        store=store,
        owner_key="local_teacher",
        conversation_id=conversation_id,
    )

    assert set(snapshot) == {
        "working_memory",
        "active_teaching_design",
    }
    assert snapshot["active_teaching_design"]["version_id"] == design.version_id
    assert snapshot["active_teaching_design"]["status"] == "awaiting_confirmation"

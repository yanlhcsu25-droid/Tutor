from calculus_agent.agent.conversation_state import DatabasePendingReplacementStore
from calculus_agent.agent.tool_adapters.teaching_design import build_teaching_design_tools
from calculus_agent.agent.tool_registry import AgentExecutionContext
from calculus_agent.teaching_design.schemas import TeachingDesignContent
from calculus_agent.teaching_design.service import TeachingDesignService


def test_discard_unconfirmed_design_clears_active_and_working_memory(session):
    conversation_id = "cancel-design"
    service = TeachingDesignService(session)
    design = service.create(
        owner_key="local_teacher",
        conversation_id=conversation_id,
        content=TeachingDesignContent(
            title="极限复习",
            objective="掌握极限运算",
            scope_names=["第一章"],
        ),
        run_id="create",
        source_user_message="设计复习",
    )
    store = DatabasePendingReplacementStore(session)
    memory = store.get_memory(conversation_id)
    memory.active_task = {
        "type": "teaching_planning",
        "status": "drafted",
        "waiting_for_scope": False,
    }
    store.set_memory(conversation_id, memory)
    context = AgentExecutionContext(
        session=session,
        conversation_id=conversation_id,
        paper_id=None,
        version_id=None,
        state_store=store,
        owner_key="local_teacher",
    )

    tool = {
        item.name: item
        for item in build_teaching_design_tools(context)
    }["discard_teaching_design"]
    result = tool.execute(tool.input_model.model_validate({}))

    assert result.status == "completed"
    assert result.payload["cancelled"] is True
    assert service.get_active(
        owner_key="local_teacher",
        conversation_id=conversation_id,
    ) is None
    assert service.get(design.version_id).status == "superseded"
    assert store.get_memory(conversation_id).active_task["status"] == "cancelled"

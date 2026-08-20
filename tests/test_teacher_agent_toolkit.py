"""Architecture regression tests for the Phase 4A Toolkit boundary."""

from pydantic import BaseModel, ConfigDict

from calculus_agent.agent.tool_registry import AgentTool, ExecutedTool
from calculus_agent.agent.toolkit import Toolkit


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


def _echo(raw: BaseModel) -> ExecutedTool:
    value = _Input.model_validate(raw)
    return ExecutedTool(
        payload={"ok": True, "value": value.value},
        status="completed",
    )


def _tool(name: str = "echo") -> AgentTool:
    return AgentTool(
        name=name,
        description="echo one integer",
        input_model=_Input,
        execute=_echo,
    )


def test_toolkit_is_the_schema_boundary():
    toolkit = Toolkit([_tool()])
    schemas = toolkit.schemas()
    assert schemas[0]["function"]["name"] == "echo"
    assert schemas[0]["function"]["parameters"]["additionalProperties"] is False


def test_toolkit_validates_arguments_before_execution():
    toolkit = Toolkit([_tool()])
    result = toolkit.execute("echo", {"value": 3, "unexpected": True})
    assert result.status == "failed"
    assert result.payload["code"] == "invalid_tool_arguments"


class _NestedContent(BaseModel):
    title: str
    tags: list[str]
    note: str


class _NestedInput(BaseModel):
    content: _NestedContent


def test_toolkit_decodes_json_strings_only_for_schema_containers():
    def execute(raw: BaseModel) -> ExecutedTool:
        values = _NestedInput.model_validate(raw)
        return ExecutedTool(
            payload={"ok": True, "content": values.content.model_dump()},
            status="completed",
        )

    toolkit = Toolkit([AgentTool(
        name="nested",
        description="nested schema",
        input_model=_NestedInput,
        execute=execute,
    )])
    result = toolkit.execute("nested", {
        "content": '{"title":"复习方案","tags":"[\\"极限\\",\\"无穷小\\"]","note":"{\\"keep\\":true}"}',
    })

    assert result.status == "completed"
    assert result.payload["content"]["tags"] == ["极限", "无穷小"]
    assert result.payload["content"]["note"] == '{"keep":true}'


def test_toolkit_normalizes_unknown_tool_failure():
    toolkit = Toolkit([_tool()])
    result = toolkit.execute("missing", {})
    assert result.status == "failed"
    assert result.payload["code"] == "unknown_tool"


def test_toolkit_can_expose_only_one_registered_group():
    toolkit = Toolkit()
    toolkit.register(_tool("paper_read"), group="paper_read")
    toolkit.register(_tool("paper_edit"), group="paper_edit")
    assert toolkit.names(groups={"paper_read"}) == ["paper_read"]


def test_duplicate_tool_name_is_rejected():
    toolkit = Toolkit([_tool()])
    try:
        toolkit.register(_tool())
    except ValueError as exc:
        assert str(exc) == "duplicate_tool_name:echo"
    else:
        raise AssertionError("duplicate tool registration must fail")


def test_teacher_agent_routes_schema_and_execution_through_toolkit():
    from pathlib import Path
    import calculus_agent.agent.agent as agent_module

    source = Path(agent_module.__file__).read_text(encoding="utf-8")
    assert "toolkit = Toolkit(tools.values())" in source
    assert "definitions = toolkit.schemas(" in source
    assert "execution = toolkit.execute(name, arguments)" in source
    assert "execute_tool(tool, arguments)" not in source

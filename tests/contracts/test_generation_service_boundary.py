"""Architecture regression tests for Phase 4B-1 generation extraction."""

from pathlib import Path

import calculus_agent.agent.tool_registry as tool_registry
import calculus_agent.api as api_module
from calculus_agent.agent.services.generation import GenerationService


def test_generation_service_is_a_real_shared_boundary():
    assert callable(GenerationService.preview)
    assert callable(GenerationService.confirm)


def test_tool_registry_delegates_generation_to_service():
    source = Path(tool_registry.__file__).read_text(encoding="utf-8")

    assert "GenerationService(" in source
    assert "generation_service.preview(" in source
    assert "generation_service.confirm()" in source

    # Business implementation must no longer live in the registry.
    assert "def _rebalance_scores(" not in source
    assert "def _merge_question_type_patch(" not in source
    assert "build_structured_generation_request(" not in source
    assert "generate_paper_from_input(" not in source


def test_generation_api_does_not_call_agent_tool_layer():
    source = Path(api_module.__file__).read_text(encoding="utf-8")

    assert "GenerationService(" in source
    assert 'build_agent_tools(context)["preview_generation_plan"]' not in source
    assert 'build_agent_tools(context)["confirm_generation_plan"]' not in source
    assert "AgentExecutionContext" not in source

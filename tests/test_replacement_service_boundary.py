"""Architecture regressions for Phase 4B-2 replacement extraction."""

from pathlib import Path

import calculus_agent.agent.tool_registry as tool_registry
from calculus_agent.agent.services.replacement import ReplacementService


def test_replacement_service_is_a_real_application_boundary():
    assert callable(ReplacementService.preview)
    assert callable(ReplacementService.confirm)
    assert callable(ReplacementService.cancel)


def test_tool_registry_delegates_replacement_to_service():
    source = Path(tool_registry.__file__).read_text(encoding="utf-8")

    assert "ReplacementService(" in source
    assert "replacement_service.preview(" in source
    assert "replacement_service.confirm()" in source
    assert "replacement_service.cancel()" in source

    # Replacement business orchestration must no longer live in the registry.
    assert "dry_run_replace_question(" not in source
    assert "apply_question_replacement(" not in source
    assert "PendingReplacement(" not in source
    assert "required_knowledge_node_ids=target_knowledge" not in source

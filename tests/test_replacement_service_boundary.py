"""Architecture regressions for unified PaperChange replacement routing."""

from pathlib import Path

import calculus_agent.agent.paper_tool_registry as paper_tool_registry
import calculus_agent.agent.tool_registry as tool_registry
from calculus_agent.agent.services.replacement import ReplacementService


def test_legacy_replacement_service_still_supports_confirmation():
    """
    ReplacementService remains only for compatibility with historical
    PendingReplacement state that may still need confirmation.
    """
    assert callable(ReplacementService.confirm)


def test_shared_tool_registry_remains_domain_agnostic():
    source = Path(tool_registry.__file__).read_text(encoding="utf-8")

    assert "ReplacementService(" not in source
    assert "dry_run_replace_question(" not in source
    assert "apply_question_replacement(" not in source
    assert "PendingReplacement(" not in source


def test_paper_registry_routes_replacement_through_current_boundaries():
    source = Path(paper_tool_registry.__file__).read_text(encoding="utf-8")

    # New model-visible replacement preview belongs to unified PaperChange.
    assert "PaperChangeService(" in source
    assert "paper_change_service.preview(" in source

    # Legacy PendingReplacement is supported only at confirmation boundary.
    assert "ReplacementService(" in source
    assert "replacement_service.confirm()" in source

    # Old model-visible replacement lifecycle must not return.
    assert "replacement_service.preview(" not in source
    assert "replacement_service.cancel(" not in source

    # Low-level replacement algorithms must stay below the registry layer.
    assert "dry_run_replace_question(" not in source
    assert "apply_question_replacement(" not in source

"""Architecture regressions for the paper-change adjustment boundary."""

from pathlib import Path

import calculus_agent.agent.paper_tool_registry as paper_tool_registry
from calculus_agent.agent.services.adjustment import AdjustmentService


def test_adjustment_service_is_a_real_application_boundary():
    assert callable(AdjustmentService.preview)
    assert callable(AdjustmentService.confirm)
    assert callable(AdjustmentService.has_pending)
    assert callable(AdjustmentService.track_plan)


def test_paper_registry_delegates_adjustment_confirmation_to_service():
    source = Path(paper_tool_registry.__file__).read_text(encoding="utf-8")

    assert "PaperWorkflow(" in source
    assert "paper_workflow.confirm(" in source
    assert "paper_workflow.preview(" in source
    assert "preview_adjust_paper(" not in source
    assert "confirm_adjust_paper(" not in source
    assert "resolve_section_item(" not in source

"""Architecture regressions for Phase 4B-3 adjustment extraction."""

from pathlib import Path

import calculus_agent.agent.tool_registry as tool_registry
from calculus_agent.agent.services.adjustment import AdjustmentService


def test_adjustment_service_is_a_real_application_boundary():
    assert callable(AdjustmentService.preview)
    assert callable(AdjustmentService.confirm)
    assert callable(AdjustmentService.has_pending)
    assert callable(AdjustmentService.track_plan)


def test_tool_registry_delegates_adjustment_lifecycle_to_service():
    source = Path(tool_registry.__file__).read_text(encoding="utf-8")

    assert "AdjustmentService(" in source
    assert "adjustment_service.preview(" in source
    assert "adjustment_service.confirm(" in source
    assert "adjustment_service.has_pending()" in source
    assert "adjustment_service.track_plan(" in source

    assert "store.get_adjustment(" not in source
    assert "store.set_adjustment(" not in source
    assert "store.clear_adjustment(" not in source
    assert "session.get(AdjustmentPlanRecord" not in source
    assert "preview_adjust_paper(" not in source
    assert "confirm_adjust_paper(" not in source
    assert "resolve_section_item(" not in source

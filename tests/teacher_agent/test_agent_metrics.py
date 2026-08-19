from calculus_agent.evaluations.metrics import score_trace
from calculus_agent.orchestration.types import TraceEntry


def _trace(step: int, actor: str, tool: str) -> TraceEntry:
    return TraceEntry(
        step=step,
        actor=actor,
        tool_name=tool,
        arguments={},
        result={},
        status="success",
        duration_ms=1,
    )


def test_trace_metrics_measure_selection_sequence_and_agents():
    traces = [
        _trace(1, "PaperOrchestratorAgent", "delegate_agent"),
        _trace(2, "KnowledgeStewardAgent", "inspect_question_supply"),
        _trace(3, "PaperOrchestratorAgent", "compose_paper"),
    ]
    metrics = score_trace(
        traces,
        expected_tools=["delegate_agent", "inspect_question_supply", "compose_paper"],
        required_agents=["PaperOrchestratorAgent", "KnowledgeStewardAgent"],
        forbidden_tools=["export_pdf"],
    )
    assert metrics.selection_precision == 1
    assert metrics.selection_recall == 1
    assert metrics.sequence_score == 1
    assert metrics.required_agent_coverage == 1
    assert metrics.forbidden_call_count == 0


def test_trace_metrics_penalize_wrong_order_and_forbidden_tool():
    traces = [
        _trace(1, "SinglePaperAgent", "compose_paper"),
        _trace(2, "SinglePaperAgent", "inspect_question_supply"),
        _trace(3, "SinglePaperAgent", "export_pdf"),
    ]
    metrics = score_trace(
        traces,
        expected_tools=["inspect_question_supply", "compose_paper"],
        required_agents=["SinglePaperAgent"],
        forbidden_tools=["export_pdf"],
    )
    assert metrics.selection_precision == 0.6667
    assert metrics.selection_recall == 1
    assert metrics.sequence_score == 0.5
    assert metrics.forbidden_call_count == 1

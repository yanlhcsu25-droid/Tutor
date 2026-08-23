from dataclasses import dataclass

from calculus_agent.runtime.trace_types import TraceEntry


@dataclass(frozen=True)
class TraceMetrics:
    selection_precision: float
    selection_recall: float
    sequence_score: float
    required_agent_coverage: float
    forbidden_call_count: int


def score_trace(
    traces: list[TraceEntry],
    *,
    expected_tools: list[str],
    required_agents: list[str],
    forbidden_tools: list[str] | None = None,
) -> TraceMetrics:
    actual_tools = [trace.tool_name for trace in traces]
    expected_set = set(expected_tools)
    actual_set = set(actual_tools)
    true_positive = len(expected_set & actual_set)
    precision = true_positive / len(actual_set) if actual_set else 0.0
    recall = true_positive / len(expected_set) if expected_set else 1.0
    sequence = _lcs_length(actual_tools, expected_tools)
    sequence_score = sequence / len(expected_tools) if expected_tools else 1.0
    actual_agents = {trace.actor for trace in traces}
    agent_set = set(required_agents)
    agent_coverage = len(actual_agents & agent_set) / len(agent_set) if agent_set else 1.0
    forbidden = set(forbidden_tools or [])
    return TraceMetrics(
        selection_precision=round(precision, 4),
        selection_recall=round(recall, 4),
        sequence_score=round(sequence_score, 4),
        required_agent_coverage=round(agent_coverage, 4),
        forbidden_call_count=sum(tool in forbidden for tool in actual_tools),
    )


def _lcs_length(left: list[str], right: list[str]) -> int:
    previous = [0] * (len(right) + 1)
    for left_item in left:
        current = [0]
        for index, right_item in enumerate(right, start=1):
            if left_item == right_item:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1]

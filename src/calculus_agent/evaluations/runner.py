import argparse
import json
from dataclasses import asdict
from pathlib import Path

from calculus_agent.config import Settings
from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.evaluations.metrics import score_trace
from calculus_agent.agent.agent import build_teacher_agent_backend, run_teacher_agent
from calculus_agent.models import TeacherAgentRunTrace
from calculus_agent.runtime.trace_types import TraceEntry


def run_evaluation(cases_path: Path, *, mode: str, settings: Settings) -> dict:
    if not settings.siliconflow_api_key:
        raise RuntimeError("评测需要配置 SILICONFLOW_API_KEY")
    cases = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    create_schema(settings.database_url)
    factory = build_session_factory(settings.database_url)
    results = []
    with factory.begin() as session:
        for case in cases:
            result = run_teacher_agent(
                session,
                case["request"],
                conversation_id=f"legacy-eval-{case['id']}",
                backend=build_teacher_agent_backend(settings),
                max_tool_rounds=case.get("max_steps", 12),
            )
            trace = session.query(TeacherAgentRunTrace).filter(
                TeacherAgentRunTrace.run_id == result.run_id
            ).one()
            traces = [
                TraceEntry(
                    step=index + 1,
                    actor="teacher_agent",
                    tool_name=str(item.get("tool_name", "unknown")),
                    arguments=dict(item.get("arguments") or {}),
                    result=dict(item.get("result") or {}),
                    status="success" if not item.get("error") else "error",
                    duration_ms=int(item.get("duration_ms") or 0),
                )
                for index, item in enumerate(trace.tool_calls_json or [])
            ]
            suffix = "multi" if mode == "multi_agent" else "single"
            metrics = score_trace(
                traces,
                expected_tools=case[f"expected_tools_{suffix}"],
                required_agents=case.get(f"required_agents_{suffix}", []),
                forbidden_tools=case.get("forbidden_tools", []),
            )
            results.append(
                {
                    "id": case["id"],
                    "status": result.status,
                    "steps": len(traces),
                    "metrics": asdict(metrics),
                }
            )
    metric_names = list(asdict(score_trace([], expected_tools=[], required_agents=[])))
    aggregate = (
        {
            name: round(sum(item["metrics"][name] for item in results) / len(results), 4)
            for name in metric_names
        }
        if results
        else {}
    )
    return {"mode": mode, "case_count": len(results), "aggregate": aggregate, "cases": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate paper-agent tool routing")
    parser.add_argument("--cases", type=Path, default=Path("evaluations/cases.jsonl"))
    parser.add_argument("--mode", choices=["single_agent", "multi_agent"], required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_evaluation(args.cases, mode=args.mode, settings=Settings())
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content)


if __name__ == "__main__":
    main()

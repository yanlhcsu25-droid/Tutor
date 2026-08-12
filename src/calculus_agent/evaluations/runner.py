import argparse
import json
from dataclasses import asdict
from pathlib import Path

from calculus_agent.config import Settings
from calculus_agent.db import build_session_factory, create_schema
from calculus_agent.evaluations.metrics import score_trace
from calculus_agent.orchestration.service import run_paper_agent
from calculus_agent.orchestration.types import TraceEntry
from calculus_agent.schemas import AgentRunRequest


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
            run = run_paper_agent(
                session,
                AgentRunRequest(
                    request=case["request"],
                    max_steps=case.get("max_steps", 12),
                    mode=mode,
                ),
                api_key=settings.siliconflow_api_key,
                base_url=settings.siliconflow_base_url,
                model=settings.siliconflow_agent_model,
                timeout=settings.siliconflow_timeout_seconds,
            )
            traces = [
                TraceEntry(
                    step=item.step,
                    actor=item.actor,
                    tool_name=item.tool_name,
                    arguments=item.arguments,
                    result=item.result,
                    status=item.status,
                    duration_ms=item.duration_ms,
                )
                for item in run.traces
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
                    "status": run.status,
                    "steps": run.steps_used,
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

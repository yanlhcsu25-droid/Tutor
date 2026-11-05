"""Safely list or run the remaining reviewed Dev cases in small batches."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from evaluation.reliability.case_loader import load_eval_suite
from evaluation.reliability.curriculum_fixture import seed_eval_curriculum
from evaluation.reliability.fixtures.paper import seed_success_question_bank
from evaluation.reliability.runner import apply_case_setup, create_eval_session

ROOT = Path(__file__).resolve().parents[4]
CASES = ROOT / "evaluation/reliability/datasets/staging/dev_v3_2_proposal/dev_122_revised_candidate.yaml"
COMPLETED_REPORTS = (
    ROOT / "evaluation/artifacts/reports/reliability/runs/dev-v3.2-dev122-batch15-first-api.json",
    ROOT / "evaluation/artifacts/reports/reliability/runs/dev-v3.2-dev122-batch30-first-api.json",
)
REPORT_DIR = ROOT / "evaluation/artifacts/reports/reliability/runs"
BATCH_SIZE = 10
SUPPORTED_GRADERS = {
    "state", "acceptance", "workflow_safety", "constraint_preservation", "score",
}


def completed_case_ids() -> set[str]:
    missing = [str(path) for path in COMPLETED_REPORTS if not path.exists()]
    if missing:
        raise SystemExit(f"缺少首次执行报告，拒绝冒险重复运行：{missing}")
    return {
        result["case_id"]
        for path in COMPLETED_REPORTS
        for result in json.loads(path.read_text())["results"]
    }


def eligible_cases():
    completed = completed_case_ids()
    suite = load_eval_suite(CASES)
    remaining = [case for case in suite.cases if case.id not in completed]
    eligible = [
        case for case in remaining
        if not (case.raw.get("review") or {}).get("open_issues")
    ]
    blocked = [case for case in remaining if case not in eligible]
    if len(completed) != 45:
        raise SystemExit(f"已完成原有 Dev 应为 45 条，实际读取到 {len(completed)} 条")
    return eligible, blocked


def preflight(cases) -> None:
    for case in cases:
        grader_types = {grader.get("type") for grader in case.graders}
        unsupported = grader_types - SUPPORTED_GRADERS
        if unsupported or not {"state", "acceptance"} <= grader_types:
            raise SystemExit(f"{case.id} Grader 未就绪：{sorted(unsupported)}")
        session = create_eval_session()
        try:
            seed_eval_curriculum(session)
            seed_success_question_bank(session)
            apply_case_setup(
                session=session,
                conversation_id=f"local-preflight-{case.id}",
                case=case,
            )
        finally:
            session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=1, help="从 1 开始的固定10条批次号")
    parser.add_argument("--delay", type=float, default=5)
    parser.add_argument("--list", action="store_true", help="只列出剩余和阻塞 Case")
    parser.add_argument("--execute", action="store_true", help="确认调用真实 API")
    args = parser.parse_args()

    if args.batch < 1:
        raise SystemExit("--batch 必须 >= 1")

    eligible, blocked = eligible_cases()
    print(f"可运行 {len(eligible)} 条；审核阻塞 {len(blocked)} 条；已完成 45 条。")
    if args.list:
        print("可运行：", " ".join(case.id for case in eligible))
        for case in blocked:
            issues = (case.raw.get("review") or {}).get("open_issues") or []
            print(f"BLOCKED {case.id}: {issues}")
        return

    start = (args.batch - 1) * BATCH_SIZE
    selected = eligible[start:start + BATCH_SIZE]
    if not selected:
        raise SystemExit("该批次没有 Case；先使用 --list 检查范围")
    preflight(selected)
    print("离线预检 PASS：", " ".join(case.id for case in selected))

    stem = f"dev-v3.2-remaining-batch{args.batch:02d}-first-api"
    report = REPORT_DIR / f"{stem}.json"
    log = REPORT_DIR / f"{stem}.log"
    if report.exists() or log.exists():
        raise SystemExit(f"报告或日志已存在，拒绝覆盖：{report} / {log}")

    command = [
        sys.executable, "evaluation/reliability/runner.py",
        "--cases-file", str(CASES.relative_to(ROOT)),
        *[part for case in selected for part in ("--case", case.id)],
        "--variant", "state-policy",
        "--case-delay-seconds", str(args.delay),
        "--report", str(report.relative_to(ROOT)),
    ]
    print("命令：", shlex.join(command))
    if not args.execute:
        print("当前为 dry-run；确认后追加 --execute。")
        return

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with log.open("x", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            output.write(line)
            output.flush()
        code = process.wait()
    if code:
        raise SystemExit(code)
    print(f"完成。请上传：{report}\n日志：{log}")


if __name__ == "__main__":
    main()

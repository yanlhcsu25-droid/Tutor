#!/usr/bin/env python3
"""Read-only Tutor Dev200 -> deduplicated task-contract audit.

Usage from Tutor repository root:
  python /path/to/tutor_dev_task_completion_audit.py \
    --dataset tests/evals/staging/tutor_workflow_eval_300_v3_1/tutor_workflow_eval_300_v3_1.yaml \
    --reports-dir tests/evals/reports --output-dir /tmp/tutor-dev-audit

Does NOT call an LLM, import Tutor runtime, or touch SQLite. Requires PyYAML.
Does NOT equate report/grader passing with a human-verified Task Completion Rate.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

CORE_GRADERS = {"state", "acceptance", "workflow_safety", "outcome_integrity"}


def stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def unwrap(x: Any) -> Any:
    if isinstance(x, str) and x.lstrip().startswith(("{", "[")):
        try:
            return json.loads(x)
        except (ValueError, TypeError):
            return x
    return x


def normalize_messages(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Full turns rather than title: retain setups/turn-level expectations in contract."""
    return [dict(t) if isinstance(t, dict) else {"user": str(t)} for t in case.get("turns", [])]


def prompt_key(case: dict[str, Any]) -> str:
    return stable([str(t.get("user", "")).strip() for t in normalize_messages(case)])


def contract_key(case: dict[str, Any]) -> str:
    # PyYAML resolves anchor/alias differences, avoiding false 'different graders'.
    payload = {k: case.get(k) for k in ("category", "subtype", "turns", "setup", "expected", "graders")}
    return stable(payload)


def build_manifest(dataset: dict[str, Any]) -> tuple[list[dict], dict[str, Any]]:
    cases = dataset.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("Dataset must provide a `cases` list")
    ids = [c.get("id") for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Dataset has duplicate case IDs")
    dev = [c for c in cases if c.get("split") == "dev"]
    by_prompt: dict[str, list[dict]] = defaultdict(list)
    for case in dev:
        by_prompt[prompt_key(case)].append(case)
    manifest: list[dict] = []
    collisions: list[dict] = []
    strict_counts: dict[str, list[dict]] = defaultdict(list)
    for group in by_prompt.values():
        variants: dict[str, list[dict]] = defaultdict(list)
        for case in group:
            variants[contract_key(case)].append(case)
        if len(variants) > 1:
            collisions.append({"case_ids": [x["id"] for x in group], "variant_count": len(variants)})
        for same_contract in variants.values():
            canonical = same_contract[0]
            strict_counts[canonical["id"]] = same_contract
            for idx, case in enumerate(same_contract):
                manifest.append({
                    "id": case["id"],
                    "canonical_id": canonical["id"],
                    "is_duplicate": idx > 0,
                    "category": case.get("category"),
                    "subtype": case.get("subtype"),
                    "review_status": (case.get("review") or {}).get("status", "unknown"),
                    "same_prompt_variant_count": len(variants),
                    "turn_count": len(normalize_messages(case)),
                })
    by_split = dict(Counter(c.get("split", "missing") for c in cases))
    canonical_rows = [m for m in manifest if not m["is_duplicate"]]
    summary = {
        "dataset_case_count": len(cases),
        "splits": by_split,
        "dev_raw_count": len(dev),
        "dev_unique_prompt_sequences": len(by_prompt),
        "dev_raw_prompt_duplicates": len(dev) - len(by_prompt),
        "dev_distinct_contracts": len(canonical_rows),
        "dev_strict_duplicates": len(dev) - len(canonical_rows),
        "same_prompt_different_contract_groups": collisions,
        "category_original": dict(sorted(Counter(c.get("category") for c in dev).items())),
        "category_contract_unique": dict(sorted(Counter(m["category"] for m in canonical_rows).items())),
        "review_status_contract_unique": dict(sorted(Counter(m["review_status"] for m in canonical_rows).items())),
        "all_dev_contracts_human_reviewed": all(m["review_status"] == "approved" for m in canonical_rows),
    }
    return manifest, summary


def report_candidates(reports_dir: Path) -> list[Path]:
    if not reports_dir.exists():
        return []
    # Regrade/targeted regression records are intentionally excluded from first-API evaluation.
    return sorted(p for p in reports_dir.rglob("*.json") if "first-api" in p.name
                  and not any(x in p.name for x in (".regraded.", ".analysis.", "postfix", "targeted")))


def get_graded_result(record: dict) -> str:
    passed = record.get("passed")
    if not isinstance(passed, bool):
        return "unverifiable"
    graders = unwrap(record.get("graders"))
    if not isinstance(graders, list) or not graders:
        return "unverifiable"
    pairs = [(g.get("grader", g.get("name", g.get("type"))), g.get("passed"))
             for g in graders if isinstance(g, dict)]
    graders_by_name = defaultdict(list)
    for name, ok in pairs:
        if name:
            graders_by_name[name].append(ok)
    if not CORE_GRADERS.issubset(graders_by_name.keys()):
        return "unverifiable"
    if passed and all(ok is True for _, ok in pairs):
        return "contract_pass"
    if passed is False or any(ok is False for _, ok in pairs):
        return "contract_fail"
    return "unverifiable"


def find_first_api(reports_dir: Path, valid_ids: set[str]) -> tuple[dict[str, Any], dict]:
    rows: dict[str, list[dict]] = defaultdict(list)
    unreadable = []
    inventories = []
    for path in report_candidates(reports_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as ex:
            unreadable.append({"file": path.name, "error_type": type(ex).__name__})
            continue
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            inventories.append({"file": path.name, "recognized_cases": 0, "note": "no results array"})
            continue
        recognized = 0
        for rec in results:
            if not isinstance(rec, dict):
                continue
            case_id = rec.get("case_id", rec.get("id"))
            if case_id not in valid_ids:
                continue
            recognized += 1
            record = {"case_id": case_id, "status": get_graded_result(rec),
                      "reported_passed": rec.get("passed"), "source": path.name,
                      "raw_hash": hashlib.sha256(stable(rec).encode()).hexdigest()}
            rows[case_id].append(record)
        inventories.append({"file": path.name, "recognized_cases": recognized})
    selections: dict[str, Any] = {}
    conflicts = []
    for case_id, candidates in rows.items():
        # A first-API case can appear in several bundles. Flag outcome conflicts instead of
        # picking the last one (which might be a repair-run, not the original trial).
        statuses = {(r["status"], r["reported_passed"]) for r in candidates}
        if len(statuses) > 1:
            conflicts.append({"id": case_id, "sources": [r["source"] for r in candidates],
                              "statuses": sorted({r["status"] for r in candidates})})
            selections[case_id] = {"status": "conflict", "sources": [r["source"] for r in candidates]}
        else:
            selections[case_id] = {"status": candidates[0]["status"],
                                   "sources": [r["source"] for r in candidates]}
    audit = {"report_files": len(inventories), "reports_inventory": inventories,
             "unreadable_files": unreadable, "case_id_conflicts": conflicts,
             "identified_case_ids": len(selections)}
    return selections, audit


def group_first_api(manifest: list[dict], raw: dict[str, Any]) -> tuple[list[dict], dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in manifest:
        grouped[entry["canonical_id"]].append(entry)
    classified = []
    for canonical, members in grouped.items():
        matched = [(m["id"], raw[m["id"]]) for m in members if m["id"] in raw]
        values = {data["status"] for _, data in matched}
        if not matched:
            status = "not_evidenced"
        elif len(values) != 1:
            status = "conflict"
        else:
            status = next(iter(values))
        classified.append({"canonical_id": canonical, "category": members[0]["category"],
                           "review_status": members[0]["review_status"], "status": status,
                           "member_ids": [m["id"] for m in members],
                           "evidence_ids": [id_ for id_, _ in matched]})
    totals = dict(Counter(x["status"] for x in classified))
    breakdown = {}
    for category in sorted({x["category"] for x in classified}):
        part = [x for x in classified if x["category"] == category]
        breakdown[category] = {"contracts": len(part), **dict(Counter(x["status"] for x in part))}
    evidence_count = totals.get("contract_pass", 0) + totals.get("contract_fail", 0)
    summary = {"distinct_contracts": len(classified), "by_status": totals,
               "with_first_api_evidence": len(classified) - totals.get("not_evidenced", 0),
               "graded_contract_count": evidence_count,
               "contract_pass_rate_on_graded_subset": round(100 * totals.get("contract_pass", 0) / evidence_count, 2) if evidence_count else None,
               "category_breakdown": breakdown,
               "metric_label": "FIRST-API AUTOMATED CONTRACT PASS RATE; NOT HUMAN-VERIFIED TCR"}
    return classified, summary


def failure_audit_summary(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"available": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    categories = Counter()
    entries = []
    for c in cases:
        details = (c.get("first_api_evidence") or {}).get("fields") or {}
        cat = unwrap(details.get("category"))
        categories[str(cat)] += 1
        entries.append({"id": c.get("case_id"), "category": cat,
                        "subtype": unwrap(details.get("subtype")),
                        "reported_passed": unwrap(details.get("passed")),
                        "source": (c.get("first_api_evidence") or {}).get("source")})
    return {"available": True, "cases": len(entries), "categories": dict(categories),
            "first_api": data.get("benchmarks_do_not_merge", {}).get("first_api"),
            "verified_offline_regrade": data.get("benchmarks_do_not_merge", {}).get("verified_offline_regrade_claim"),
            "case_details": entries}


def markdown(result: dict) -> str:
    m = result["manifest_summary"]
    f = result["first_api_summary"]
    e = result["first_api_report_audit"]
    audit = result["failure_evidence"]
    lines = ["# Tutor Dev200 去重与任务完成率只读审计", "",
        f"- 生成时间：{result['generated_utc']}",
        f"- 数据集：`{result['dataset_path']}`",
        f"- 数据集 SHA256：`{result['dataset_sha256']}`", "",
        "## 1. 数据集结构", "",
        "| 口径 | 数量 |", "|---|---:|",
        f"| 全部测试记录 | {m['dataset_case_count']} |",
        f"| Dev 原始记录 | {m['dev_raw_count']} |",
        f"| Dev 不同用户输入序列 | {m['dev_unique_prompt_sequences']} |",
        f"| Dev 文本重复数 | {m['dev_raw_prompt_duplicates']} |",
        f"| Dev 按完整业务契约去重后 | {m['dev_distinct_contracts']} |",
        f"| 同文本但不同契约组 | {len(m['same_prompt_different_contract_groups'])} |", "",
        "| 类别 | 原始Dev | 严格去重后 |", "|---|---:|---:|",]
    for cat in sorted(m["category_original"]):
        lines.append(f"| {cat} | {m['category_original'].get(cat,0)} | {m['category_contract_unique'].get(cat,0)} |")
    lines += ["", f"人工审核状态：`{m['review_status_contract_unique']}`。",
        "Dev 尚未全部人工审核，不应把自动化报告通过率直接称作正式业务 TCR。", "",
        "## 2. 历史首测证据映射", "",
        f"- 识别出首测批次文件：{e['report_files']} 份",
        f"- 识别出的 Dev Case ID：{e['identified_case_ids']} 个",
        f"- 映射到不同严格业务契约：{f['with_first_api_evidence']} / {f['distinct_contracts']}",
        f"- 可核对核心 Grader 的契约数：{f['graded_contract_count']}",
        f"- 契约结果计数：`{f['by_status']}`", "",
        "| 类别 | 去重契约数 | 契约通过 | 契约失败 | 未找到原始证据 |", "|---|---:|---:|---:|---:|",]
    for cat, v in f["category_breakdown"].items():
        lines.append(f"| {cat} | {v['contracts']} | {v.get('contract_pass',0)} | {v.get('contract_fail',0)} | {v.get('not_evidenced',0)} |")
    lines += ["", f"自动化契约通过率（有Grader证据子集）：**{f['contract_pass_rate_on_graded_subset']}%**。",
        "该比率是首测自动化契约通过率的代理指标；未人工确认业务状态与全部Oracle，不应直接宣传为正式Task Completion Rate。", "",
        "## 3. 已有失败审计证据", ""]
    if audit.get("available"):
        lines += [f"- 原始首测摘要：{audit['first_api']}", f"- 已核验离线复评摘要：{audit['verified_offline_regrade']}",
                  f"- 失败证据仅覆盖：{audit['cases']}个历史失败 Case，类别分布：{audit['categories']}。",
                  "- 离线复评不等于再跑模型，不能改写首次执行分子。"]
    else:
        lines.append("未提供历史失败审计附件；不能复核97/117或104/117的全量原始结果。")
    lines += ["", "## 4. 不能合并的成绩与限制", "",
              "- Dev200 文本去重、严格业务契约去重、Dev117 首测、Dev117 离线复评、Holdout69是不同口径。",
              "- 相同任务文本在不同Fixture/Oracle下可能是不同任务；仅在完整契约相同的条件下进行严格合并。",
              "- 历史批次缺失、同一ID多报告冲突及缺失核心Grader均不计成功。",
              "- 如要声明最终任务完成率，还需人工审核Fixture/Oracle并核查执行末态；不要求重新调用模型。", "",
              "## 5. 本次操作安全边界", "",
              "仅读取原始YAML和JSON。没有模型调用、数据库写入、重评分或历史报告覆盖。"]
    if e["case_id_conflicts"]:
        lines += ["", f"警告：检测到 {len(e['case_id_conflicts'])} 个Case ID对应不同首测结果，未擅自选择记录。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("tests/evals/staging/tutor_workflow_eval_300_v3_1/tutor_workflow_eval_300_v3_1.yaml"))
    parser.add_argument("--reports-dir", type=Path, default=Path("tests/evals/reports"))
    parser.add_argument("--failure-audit", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/tutor-dev-task-audit"))
    args = parser.parse_args()
    contents = args.dataset.read_bytes()
    data = yaml.safe_load(contents)
    manifest, summary = build_manifest(data)
    raw, reports_info = find_first_api(args.reports_dir, {x["id"] for x in manifest})
    results, completion = group_first_api(manifest, raw)
    result = {"generated_utc": datetime.now(timezone.utc).isoformat(),
              "dataset_path": str(args.dataset), "dataset_sha256": hashlib.sha256(contents).hexdigest(),
              "manifest_summary": summary,
              "first_api_report_audit": reports_info,
              "first_api_summary": completion,
              "failure_evidence": failure_audit_summary(args.failure_audit)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "tutor_dev_task_completion_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "tutor_dev_task_completion_audit.md").write_text(markdown(result), encoding="utf-8")
    for file_name, items in (("dev_dedup_manifest.csv", manifest), ("dev_contract_execution_status.csv", results)):
        with (args.output_dir / file_name).open("w", encoding="utf-8-sig", newline="") as out:
            w = csv.DictWriter(out, fieldnames=list(items[0]) if items else [])
            w.writeheader()
            for item in items:
                w.writerow({k: stable(v) if isinstance(v, list | dict) else v for k,v in item.items()})
    print(json.dumps({"manifest": summary, "historical_first_api": {k:v for k,v in completion.items() if k not in ("category_breakdown",)}},ensure_ascii=False,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

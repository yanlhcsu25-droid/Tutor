import argparse
import json
import re
from collections import Counter
from pathlib import Path
from statistics import median

from calculus_agent.datasets.mm_math import _fingerprint, _records

EXPECTED_FIELDS = ("question", "solution", "difficult", "year", "knowledge", "file_name")


def audit_mm_math(path: Path) -> dict:
    records = list(_records(path))
    questions = [str(record.get("question") or "") for record in records]
    solutions = [str(record.get("solution") or "") for record in records]
    fingerprints = Counter(_fingerprint(question) for question in questions if question)
    knowledge_level_1 = Counter()
    knowledge_level_2 = Counter()
    for record in records:
        knowledge = record.get("knowledge")
        if isinstance(knowledge, dict):
            knowledge_level_1[str(knowledge.get("level_1") or "<missing>")] += 1
            knowledge_level_2[str(knowledge.get("level_2") or "<missing>")] += 1

    cjk_questions = sum(bool(re.search(r"[\u4e00-\u9fff]", question)) for question in questions)
    image_dependent = sum(
        bool(re.search(r"\b(as shown|shown in the figure|diagram|graph below)\b", question, re.I))
        for question in questions
    )
    boxed_solutions = sum(r"\boxed{" in solution for solution in solutions)

    return {
        "source": "THU-KEG/MM_Math",
        "rows": len(records),
        "fields": sorted({key for record in records for key in record}),
        "missing": {
            field: sum(record.get(field) in (None, "") for record in records)
            for field in EXPECTED_FIELDS
        },
        "types": {
            field: dict(sorted(Counter(type(record.get(field)).__name__ for record in records).items()))
            for field in EXPECTED_FIELDS
        },
        "year_distribution": dict(sorted(Counter(record.get("year") for record in records).items())),
        "difficulty_distribution": dict(
            sorted(Counter(record.get("difficult") for record in records).items())
        ),
        "knowledge_level_1": dict(knowledge_level_1.most_common()),
        "knowledge_level_2": dict(knowledge_level_2.most_common()),
        "language": {
            "questions_with_cjk": cjk_questions,
            "questions_without_cjk": len(questions) - cjk_questions,
        },
        "multimodal": {
            "records_with_file_name": sum(bool(record.get("file_name")) for record in records),
            "likely_image_dependent_questions": image_dependent,
        },
        "answers": {
            "solutions_with_boxed_answer": boxed_solutions,
            "solutions_without_boxed_answer": len(solutions) - boxed_solutions,
        },
        "duplicates": {
            "duplicate_fingerprint_groups": sum(count > 1 for count in fingerprints.values()),
            "rows_in_duplicate_groups": sum(
                count for count in fingerprints.values() if count > 1
            ),
        },
        "lengths": {
            "question": _length_summary(questions),
            "solution": _length_summary(solutions),
        },
        "fitness": {
            "direct_chinese_paper_generation": cjk_questions == len(questions),
            "text_only_complete": image_dependent == 0,
            "supports_choice_fill_mix": False,
            "recommended_use": (
                "Use as English, open-ended, multimodal source material. "
                "Add reviewed Chinese localization before Chinese paper export."
            ),
        },
    }


def render_markdown(report: dict) -> str:
    rows = report["rows"]
    language = report["language"]
    multimodal = report["multimodal"]
    answers = report["answers"]
    duplicates = report["duplicates"]
    lines = [
        "# MM-Math 数据审计",
        "",
        f"- 记录数：{rows}",
        f"- 字段：{', '.join(report['fields'])}",
        f"- 含中文题干：{language['questions_with_cjk']} / {rows}",
        (
            "- 疑似依赖图片的题目："
            f"{multimodal['likely_image_dependent_questions']} / {rows}"
        ),
        f"- 含 `\\boxed{{}}` 答案：{answers['solutions_with_boxed_answer']} / {rows}",
        f"- 重复指纹组：{duplicates['duplicate_fingerprint_groups']}",
        "",
        "## 年级分布",
        "",
        *[f"- {key}: {value}" for key, value in report["year_distribution"].items()],
        "",
        "## 难度分布",
        "",
        *[f"- {key}: {value}" for key, value in report["difficulty_distribution"].items()],
        "",
        "## 一级知识点",
        "",
        *[f"- {key}: {value}" for key, value in report["knowledge_level_1"].items()],
        "",
        "## 结论",
        "",
        "- 该 JSONL 是英文题干与英文解析，不能未经本地化直接生成中文试卷。",
        "- `knowledge` 是 `level_1/level_2` 对象，不是字符串数组。",
        "- `year` 使用 `seven/eight/nine`，难度使用 `easy/medium/hard`。",
        "- 数据全部按开放题使用，不能单独支撑选择题、填空题配额。",
        "- 图片未下载时，疑似看图题不能视为完整可用题。",
        "",
    ]
    return "\n".join(lines)


def _length_summary(values: list[str]) -> dict[str, int]:
    lengths = sorted(len(value) for value in values)
    if not lengths:
        return {"min": 0, "median": 0, "p95": 0, "max": 0}
    return {
        "min": lengths[0],
        "median": int(median(lengths)),
        "p95": lengths[min(len(lengths) - 1, int(len(lengths) * 0.95))],
        "max": lengths[-1],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the official MM-Math JSONL")
    parser.add_argument("path", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    report = audit_mm_math(args.path)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

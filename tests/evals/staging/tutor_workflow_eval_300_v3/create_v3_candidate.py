"""Create V3 from the immutable V2 staging source without changing case content."""

from pathlib import Path

import yaml

SOURCE = Path("tests/evals/staging/tutor_workflow_eval_300_v2/tutor_workflow_eval_300_v2.yaml")
OUTPUT = Path("tests/evals/staging/tutor_workflow_eval_300_v3/tutor_workflow_eval_300_v3.yaml")
# Keep every category/subtype family in exactly one split. Total: 100 Holdout cases.
HOLDOUT_FAMILIES = {
    ("generation", "shortage_or_conflict"),
    ("pending", "cancel"),
    ("paper", "delete_preview"),
    ("paper", "replace_preview"),
    ("teaching", "design_create"),
    ("ambiguity", "missing_context_or_referent"),
    ("adversarial", "instruction_boundary"),
    ("multi_turn", "generate_then_confirm"),
}


def main() -> None:
    payload = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    payload["version"] = "3.0"
    payload["suite"] = {
        "name": "tutor_workflow_eval_300_v3",
        "description": (
            "V2 的可追溯 staging 衍生候选集；category/subtype family 不跨 Dev/Holdout。"
            "仍须完成业务 oracle 与人工审核后方可冻结。"
        ),
        "splits": {"dev": 200, "holdout_candidate": 100},
        "derived_from": str(SOURCE),
    }
    for case in payload["cases"]:
        family = (case["category"], case["subtype"])
        case["split"] = "holdout_candidate" if family in HOLDOUT_FAMILIES else "dev"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    main()

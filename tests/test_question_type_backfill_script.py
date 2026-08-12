import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/migrations/backfill_legacy_question_types.py"
SPEC = importlib.util.spec_from_file_location("question_type_backfill", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
replace_markdown_question_type = MODULE.replace_markdown_question_type


def test_replace_markdown_question_type_changes_only_type_value():
    before = (
        "## 题目内容\n\n求极限\n\n"
        "## 参考解答\n\n答案不变\n\n"
        "## 题型\n\nother\n\n"
        "## 原始题号\n\n1\n"
    )
    after = replace_markdown_question_type(before, "calculation")
    assert after == before.replace("\nother\n", "\ncalculation\n", 1)


def test_replace_refuses_non_legacy_or_ambiguous_markdown():
    with pytest.raises(ValueError):
        replace_markdown_question_type("## 题型\n\nproof\n", "calculation")
    with pytest.raises(ValueError):
        replace_markdown_question_type(
            "## 题型\n\nother\n\n## 题型\n\nother\n", "calculation"
        )

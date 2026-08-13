"""Deterministic tools used by the Teacher Agent."""

from .paper_tools import generate_paper_tool
from .replacement_tools import (
    apply_question_replacement,
    dry_run_replace_question,
    run_replacement_dry_run,
)
from .version_tools import run_version_operation

__all__ = [
    "apply_question_replacement", "dry_run_replace_question", "generate_paper_tool",
    "run_replacement_dry_run",
    "run_version_operation",
]

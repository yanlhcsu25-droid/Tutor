"""Phase 2A teacher-requirement parsing layer."""

from .requirement_parser import parse_teacher_requirement
from .schemas import RequirementBlueprint
from .blueprint_adapter import (
    BlueprintBuildResult,
    build_generation_request,
    build_paper_blueprint,
    dry_run_requirement,
    resolve_generation_scope,
)
from .schemas import GenerationConstraints, PaperGenerationRequest

__all__ = [
    "BlueprintBuildResult",
    "GenerationConstraints",
    "PaperGenerationRequest",
    "build_generation_request",
    "RequirementBlueprint",
    "build_paper_blueprint",
    "dry_run_requirement",
    "parse_teacher_requirement",
    "resolve_generation_scope",
    "TeacherAgentResult",
    "run_teacher_agent",
    "GeneratePaperToolResult",
    "PaperSummary",
    "generate_paper_tool",
]


def __getattr__(name: str):
    """Lazy-load orchestration exports to avoid selector import cycles."""
    if name in {"TeacherAgentResult", "run_teacher_agent"}:
        from .agent import TeacherAgentResult, run_teacher_agent
        return {"TeacherAgentResult": TeacherAgentResult, "run_teacher_agent": run_teacher_agent}[name]
    if name in {"GeneratePaperToolResult", "PaperSummary", "generate_paper_tool"}:
        from .tools.paper_tools import GeneratePaperToolResult, PaperSummary, generate_paper_tool
        return {
            "GeneratePaperToolResult": GeneratePaperToolResult,
            "PaperSummary": PaperSummary,
            "generate_paper_tool": generate_paper_tool,
        }[name]
    raise AttributeError(name)

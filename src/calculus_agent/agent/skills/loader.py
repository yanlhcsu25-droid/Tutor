"""Deterministic loader for Teacher Agent SKILL.md resources."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


_ALLOWED_SKILLS = frozenset({
    "paper_question_operations",
})


@lru_cache(maxsize=None)
def load_skill(name: str) -> str:
    """Load one approved SKILL.md resource."""
    if name not in _ALLOWED_SKILLS:
        raise ValueError(f"Unknown Teacher Agent skill: {name}")

    resource = (
        files("calculus_agent.agent.skills")
        .joinpath(name)
        .joinpath("SKILL.md")
    )

    if not resource.is_file():
        raise FileNotFoundError(
            f"Teacher Agent skill file missing: {name}/SKILL.md"
        )

    return resource.read_text(encoding="utf-8")


def load_skill_bundle(name: str) -> str:
    """Return model-ready Skill text with explicit boundaries."""
    content = load_skill(name).strip()
    return (
        f'<active_skill name="{name}">\n'
        f"{content}\n"
        "</active_skill>"
    )

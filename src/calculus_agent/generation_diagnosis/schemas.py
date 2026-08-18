from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SolverStatus = Literal[
    "not_run",
    "precheck_failed",
    "optimal",
    "feasible",
    "infeasible",
    "model_invalid",
    "unknown",
]

FailureClass = Literal[
    "design",
    "artifact",
    "execution",
    "technical",
    "unknown",
]

Recoverability = Literal[
    "retry_same_design",
    "repair_without_design_change",
    "requires_design_revision",
    "technical_intervention",
    "unknown",
]

EvidenceSource = Literal[
    "candidate_pool",
    "compiled_constraints",
    "solver",
    "validation",
    "exception",
]


class SelectionEvidence(BaseModel):
    """Immutable facts captured from the exact selector candidate pool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    type_supply: dict[str, int] = Field(default_factory=dict)
    knowledge_supply: dict[str, int] = Field(default_factory=dict)
    image_supply: int = Field(default=0, ge=0)
    missing_required_question_ids: list[str] = Field(default_factory=list)
    solver_status: SolverStatus = "not_run"


class DiagnosisFact(BaseModel):
    """Machine-readable evidence supporting a diagnosis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: str
    subject: str | None = None
    required: int | float | str | None = None
    available: int | float | str | None = None
    actual: int | float | str | bool | None = None
    source: EvidenceSource


class GenerationDiagnosis(BaseModel):
    """Deterministic classification of a generation failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_class: FailureClass
    code: str
    recoverability: Recoverability
    facts: list[DiagnosisFact] = Field(default_factory=list)

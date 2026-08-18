"""TeachingDesign business domain.

This package owns the durable teaching-design source of truth.
It must not depend on Teacher Agent runtime/tool orchestration.
"""

from .schemas import (
    AssessmentPlan,
    EvidenceReference,
    KnowledgePlanItem,
    LecturePlan,
    TeachingDesignContent,
    TeachingDesignPatch,
    TeachingDesignRead,
    TeachingDesignStatus,
    TeachingStep,
)
from .service import (
    StaleTeachingDesignError,
    TeachingDesignNotFoundError,
    TeachingDesignService,
    TeachingDesignStateError,
)

__all__ = [
    "AssessmentPlan",
    "EvidenceReference",
    "KnowledgePlanItem",
    "LecturePlan",
    "TeachingDesignContent",
    "TeachingDesignPatch",
    "TeachingDesignRead",
    "TeachingDesignStatus",
    "TeachingStep",
    "StaleTeachingDesignError",
    "TeachingDesignNotFoundError",
    "TeachingDesignService",
    "TeachingDesignStateError",
]

from __future__ import annotations

from calculus_agent.schemas import PaperBlueprint, PaperPreviewRead, ValidationReportRead

from .schemas import DiagnosisFact, GenerationDiagnosis, SelectionEvidence


def _required_type_counts(blueprint: PaperBlueprint) -> dict[str, int]:
    if blueprint.sections:
        return {
            section.question_type: section.count
            for section in blueprint.sections
        }
    return dict(blueprint.question_type_counts)


def diagnose_generation_failure(
    *,
    blueprint: PaperBlueprint,
    evidence: SelectionEvidence | None = None,
    preview: PaperPreviewRead | None = None,
    validation_report: ValidationReportRead | None = None,
    technical_error: BaseException | None = None,
) -> GenerationDiagnosis:
    """Classify generation failure using only deterministic execution facts.

    This function is intentionally read-only:
    - no database/session dependency
    - no TeachingDesign mutation
    - no Pending mutation
    - no Paper mutation
    - no warning/error-string parsing
    """

    if technical_error is not None:
        return GenerationDiagnosis(
            failure_class="technical",
            code="technical_failure",
            recoverability="technical_intervention",
            facts=[
                DiagnosisFact(
                    dimension="exception",
                    subject=type(technical_error).__name__,
                    source="exception",
                )
            ],
        )

    if validation_report is not None and not validation_report.passed:
        return GenerationDiagnosis(
            failure_class="artifact",
            code="paper_validation_failed",
            recoverability="repair_without_design_change",
            facts=[
                DiagnosisFact(
                    dimension="paper_validation",
                    actual=False,
                    source="validation",
                )
            ],
        )

    if evidence is None:
        return GenerationDiagnosis(
            failure_class="unknown",
            code="unknown_generation_failure",
            recoverability="unknown",
        )

    if evidence.solver_status == "model_invalid":
        return GenerationDiagnosis(
            failure_class="technical",
            code="technical_failure",
            recoverability="technical_intervention",
            facts=[
                DiagnosisFact(
                    dimension="solver_status",
                    actual=evidence.solver_status,
                    source="solver",
                )
            ],
        )

    if evidence.eligible_count == 0:
        return GenerationDiagnosis(
            failure_class="design",
            code="no_eligible_candidates",
            recoverability="requires_design_revision",
            facts=[
                DiagnosisFact(
                    dimension="eligible_candidates",
                    required=blueprint.total_questions,
                    available=0,
                    source="candidate_pool",
                )
            ],
        )

    if evidence.missing_required_question_ids:
        return GenerationDiagnosis(
            failure_class="design",
            code="required_question_unavailable",
            recoverability="requires_design_revision",
            facts=[
                DiagnosisFact(
                    dimension="required_question",
                    subject=question_id,
                    required=1,
                    available=0,
                    source="candidate_pool",
                )
                for question_id in evidence.missing_required_question_ids
            ],
        )

    type_shortages: list[DiagnosisFact] = []
    for question_type, required in _required_type_counts(blueprint).items():
        available = evidence.type_supply.get(question_type, 0)
        if available < required:
            type_shortages.append(
                DiagnosisFact(
                    dimension="question_type",
                    subject=question_type,
                    required=required,
                    available=available,
                    source="candidate_pool",
                )
            )

    if type_shortages:
        return GenerationDiagnosis(
            failure_class="design",
            code="type_supply_shortage",
            recoverability="requires_design_revision",
            facts=type_shortages,
        )

    knowledge_shortages: list[DiagnosisFact] = []
    for quota in blueprint.knowledge_quotas:
        available = evidence.knowledge_supply.get(quota.name, 0)
        if available < quota.count:
            knowledge_shortages.append(
                DiagnosisFact(
                    dimension="required_knowledge",
                    subject=quota.name,
                    required=quota.count,
                    available=available,
                    source="candidate_pool",
                )
            )

    if knowledge_shortages:
        return GenerationDiagnosis(
            failure_class="design",
            code="required_knowledge_supply_shortage",
            recoverability="requires_design_revision",
            facts=knowledge_shortages,
        )

    # These are deterministically observable shortages, but T4-2A deliberately
    # keeps the public failure-code taxonomy small. They remain facts under the
    # generic joint-constraint diagnosis rather than inventing premature codes.
    joint_facts: list[DiagnosisFact] = []

    if evidence.eligible_count < blueprint.total_questions:
        joint_facts.append(
            DiagnosisFact(
                dimension="total_questions",
                required=blueprint.total_questions,
                available=evidence.eligible_count,
                source="candidate_pool",
            )
        )

    if evidence.image_supply < blueprint.image_question_count:
        joint_facts.append(
            DiagnosisFact(
                dimension="image_question_count",
                required=blueprint.image_question_count,
                available=evidence.image_supply,
                source="candidate_pool",
            )
        )

    if evidence.solver_status in {"infeasible", "precheck_failed"}:
        joint_facts.append(
            DiagnosisFact(
                dimension="solver_status",
                actual=evidence.solver_status,
                source="solver",
            )
        )
        return GenerationDiagnosis(
            failure_class="design",
            code="joint_constraints_infeasible",
            recoverability="requires_design_revision",
            facts=joint_facts,
        )

    if preview is not None and not preview.feasible:
        joint_facts.append(
            DiagnosisFact(
                dimension="preview_feasible",
                actual=False,
                source="solver",
            )
        )
        return GenerationDiagnosis(
            failure_class="design",
            code="joint_constraints_infeasible",
            recoverability="requires_design_revision",
            facts=joint_facts,
        )

    if evidence.solver_status == "unknown":
        return GenerationDiagnosis(
            failure_class="execution",
            code="solver_status_unknown",
            recoverability="retry_same_design",
            facts=[
                DiagnosisFact(
                    dimension="solver_status",
                    actual="unknown",
                    source="solver",
                )
            ],
        )

    return GenerationDiagnosis(
        failure_class="unknown",
        code="unknown_generation_failure",
        recoverability="unknown",
        facts=joint_facts,
    )

from copy import deepcopy
from types import SimpleNamespace

from calculus_agent.generation_diagnosis import (
    SelectionEvidence,
    diagnose_generation_failure,
)
from calculus_agent.schemas import KnowledgeQuota, PaperBlueprint


def _blueprint(**updates) -> PaperBlueprint:
    values = {
        "title": "T4-2A diagnosis",
        "total_questions": 4,
        "total_score": 20,
        "question_type_counts": {"计算题": 2, "证明题": 2},
        "seed": 42,
    }
    values.update(updates)
    return PaperBlueprint(**values)


def _evidence(**updates) -> SelectionEvidence:
    values = {
        "candidate_count": 8,
        "eligible_count": 8,
        "type_supply": {"计算题": 4, "证明题": 4},
        "knowledge_supply": {},
        "image_supply": 0,
        "missing_required_question_ids": [],
        "solver_status": "infeasible",
    }
    values.update(updates)
    return SelectionEvidence(**values)


def test_no_eligible_candidates():
    diagnosis = diagnose_generation_failure(
        blueprint=_blueprint(),
        evidence=_evidence(
            candidate_count=0,
            eligible_count=0,
            type_supply={},
        ),
    )

    assert diagnosis.code == "no_eligible_candidates"
    assert diagnosis.failure_class == "design"
    assert diagnosis.recoverability == "requires_design_revision"


def test_type_supply_shortage_uses_real_supply():
    diagnosis = diagnose_generation_failure(
        blueprint=_blueprint(),
        evidence=_evidence(
            type_supply={"计算题": 4, "证明题": 1},
        ),
    )

    assert diagnosis.code == "type_supply_shortage"
    assert len(diagnosis.facts) == 1
    assert diagnosis.facts[0].subject == "证明题"
    assert diagnosis.facts[0].required == 2
    assert diagnosis.facts[0].available == 1
    assert diagnosis.facts[0].source == "candidate_pool"


def test_required_knowledge_supply_shortage():
    blueprint = _blueprint(
        knowledge_quotas=[
            KnowledgeQuota(name="拉格朗日中值定理", count=2),
        ],
    )
    diagnosis = diagnose_generation_failure(
        blueprint=blueprint,
        evidence=_evidence(
            knowledge_supply={"拉格朗日中值定理": 1},
        ),
    )

    assert diagnosis.code == "required_knowledge_supply_shortage"
    assert diagnosis.facts[0].subject == "拉格朗日中值定理"
    assert diagnosis.facts[0].required == 2
    assert diagnosis.facts[0].available == 1


def test_joint_constraints_infeasible_when_simple_supply_is_sufficient():
    diagnosis = diagnose_generation_failure(
        blueprint=_blueprint(),
        evidence=_evidence(
            type_supply={"计算题": 4, "证明题": 4},
            solver_status="infeasible",
        ),
    )

    assert diagnosis.code == "joint_constraints_infeasible"
    assert diagnosis.failure_class == "design"
    assert any(
        fact.dimension == "solver_status"
        and fact.actual == "infeasible"
        for fact in diagnosis.facts
    )


def test_validation_failure_is_artifact_failure():
    diagnosis = diagnose_generation_failure(
        blueprint=_blueprint(),
        evidence=_evidence(solver_status="optimal"),
        validation_report=SimpleNamespace(passed=False),
    )

    assert diagnosis.code == "paper_validation_failed"
    assert diagnosis.failure_class == "artifact"
    assert diagnosis.recoverability == "repair_without_design_change"


def test_technical_exception_is_not_misreported_as_design_failure():
    diagnosis = diagnose_generation_failure(
        blueprint=_blueprint(),
        evidence=_evidence(),
        technical_error=RuntimeError("database unavailable"),
    )

    assert diagnosis.code == "technical_failure"
    assert diagnosis.failure_class == "technical"
    assert diagnosis.facts[0].subject == "RuntimeError"


def test_warning_text_is_not_a_diagnosis_input():
    evidence = _evidence(
        type_supply={"计算题": 4, "证明题": 1},
    )

    left = diagnose_generation_failure(
        blueprint=_blueprint(),
        evidence=evidence,
        preview=SimpleNamespace(
            feasible=False,
            warnings=["旧中文文案"],
        ),
    )
    right = diagnose_generation_failure(
        blueprint=_blueprint(),
        evidence=evidence,
        preview=SimpleNamespace(
            feasible=False,
            warnings=["完全不同的文案"],
        ),
    )

    assert left == right
    assert left.code == "type_supply_shortage"


def test_diagnosis_is_read_only_for_inputs():
    blueprint = _blueprint()
    evidence = _evidence()
    blueprint_before = deepcopy(blueprint.model_dump(mode="json"))
    evidence_before = deepcopy(evidence.model_dump(mode="json"))

    diagnose_generation_failure(
        blueprint=blueprint,
        evidence=evidence,
    )

    assert blueprint.model_dump(mode="json") == blueprint_before
    assert evidence.model_dump(mode="json") == evidence_before

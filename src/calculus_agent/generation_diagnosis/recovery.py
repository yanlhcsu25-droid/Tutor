from __future__ import annotations

from .schemas import GenerationDiagnosis, RecoveryAction, RecoveryActionType


def decide_recovery(diagnosis: GenerationDiagnosis) -> RecoveryAction:
    """Choose the allowed next step from deterministic diagnosis facts."""
    code = diagnosis.code

    if code in {"insufficient_candidates", "no_eligible_candidates", "type_supply_shortage", "required_knowledge_supply_shortage", "required_question_unavailable"}:
        return RecoveryAction(
            action_type=RecoveryActionType.ASK_USER,
            reason="题库资源不足，需要调整教学要求",
            options=["扩大章节范围", "降低题型比例", "调整难度要求", "补充题库"],
        )
    if code == "scope_not_found":
        return RecoveryAction(
            action_type=RecoveryActionType.ASK_USER,
            reason="无法确定教学范围，需要确认章节或知识点",
        )
    if code == "score_rebalance_ambiguous":
        return RecoveryAction(
            action_type=RecoveryActionType.ASK_USER,
            reason="题型比例与总分之间存在歧义，需要确认调整方式",
            options=["修改题型比例", "修改总分", "接受系统重新分配"],
        )
    if code == "pending_generation_exists":
        return RecoveryAction(
            action_type=RecoveryActionType.ASK_USER,
            reason="当前已有待确认组卷方案，需要先处理当前方案",
            options=["继续当前修改", "放弃当前修改"],
        )
    if code == "generation_partial_patch_required":
        return RecoveryAction(
            action_type=RecoveryActionType.ADJUST_CONSTRAINTS,
            reason="当前请求需要补充或调整组卷约束后重新预览",
        )
    if code == "teaching_design_not_executable":
        return RecoveryAction(
            action_type=RecoveryActionType.REVISE_DESIGN,
            reason="当前教学设计无法被执行，需要重新调整教学设计",
        )
    if code in {"technical_failure", "generation_state_store_unavailable", "paper_persistence_failed"}:
        return RecoveryAction(
            action_type=RecoveryActionType.AUTO_RETRY,
            reason="系统执行失败，可以重试当前生成操作",
        )
    return RecoveryAction(
        action_type=RecoveryActionType.ABORT,
        reason="未知错误，需要人工处理",
    )

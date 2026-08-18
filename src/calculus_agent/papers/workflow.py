from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from calculus_agent.models import (
    ConstraintViolation,
    KnowledgeNode,
    Paper,
    PaperBlueprintRecord,
    PaperItem,
    PaperOperationHistory,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    ValidationReport,
)
from calculus_agent.agent.schemas import GenerationConstraints
from calculus_agent.papers.selector import compose_paper
from calculus_agent.schemas import (
    BlueprintCreateRead,
    ConstraintCheck,
    ConstraintViolationRead,
    PaperBlueprint,
    PaperItemRead,
    PaperOperationRead,
    PaperPreviewRead,
    SavedPaperRead,
    SupplyCheckRead,
    ValidationReportRead,
)


class WorkflowNotFoundError(LookupError):
    pass


class BlueprintStateError(ValueError):
    pass


class InfeasiblePaperError(ValueError):
    def __init__(self, violations: list[ConstraintViolationRead]):
        self.violations = violations
        super().__init__("题库无法满足组卷约束")


def save_blueprint(session: Session, blueprint: PaperBlueprint) -> BlueprintCreateRead:
    record = PaperBlueprintRecord(
        title=blueprint.title, blueprint_json=blueprint.model_dump(mode="json"), status="draft"
    )
    session.add(record)
    session.flush()
    return _blueprint_read(record)


def get_blueprint(session: Session, blueprint_id: str) -> BlueprintCreateRead:
    record = session.get(PaperBlueprintRecord, blueprint_id)
    if record is None:
        raise WorkflowNotFoundError("Blueprint not found")
    return _blueprint_read(record)


def update_blueprint(
    session: Session, blueprint_id: str, blueprint: PaperBlueprint
) -> BlueprintCreateRead:
    record = session.get(PaperBlueprintRecord, blueprint_id)
    if record is None:
        raise WorkflowNotFoundError("Blueprint not found")
    if record.status != "draft":
        raise BlueprintStateError("只有草稿蓝图可以修改")
    record.title = blueprint.title
    record.blueprint_json = blueprint.model_dump(mode="json")
    record.updated_at = datetime.now(UTC)
    session.flush()
    return _blueprint_read(record)


def confirm_blueprint(session: Session, blueprint_id: str) -> BlueprintCreateRead:
    record = session.get(PaperBlueprintRecord, blueprint_id)
    if record is None:
        raise WorkflowNotFoundError("Blueprint not found")
    if record.status != "draft":
        raise BlueprintStateError("蓝图已确认或已使用")
    # Revalidation ensures persisted JSON still obeys every hard rule.
    PaperBlueprint.model_validate(record.blueprint_json)
    record.status = "confirmed"
    record.updated_at = datetime.now(UTC)
    session.flush()
    return _blueprint_read(record)


def check_blueprint_supply(session: Session, blueprint_id: str) -> SupplyCheckRead:
    record = session.get(PaperBlueprintRecord, blueprint_id)
    if record is None:
        raise WorkflowNotFoundError("Blueprint not found")
    preview = compose_paper(session, PaperBlueprint.model_validate(record.blueprint_json))
    violations = _constraint_violations(preview.constraints)
    return SupplyCheckRead(
        feasible=preview.feasible,
        violations=violations,
        suggestions=_supply_suggestions(violations),
        warnings=[warning for warning in preview.warnings if warning.startswith("目标知识点")],
    )


def _supply_suggestions(violations: list[ConstraintViolationRead]) -> list[str]:
    suggestions: list[str] = []
    for item in violations:
        if item.code == "QUESTION_COUNT_SHORTAGE":
            suggestions.append("降低总题数，或允许从相邻章节补充题目。")
        elif item.code == "QUESTION_TYPE_SHORTAGE":
            suggestions.append(f"降低“{item.field}”的题目数量，或允许其他题型替代。")
        elif item.code == "KNOWLEDGE_SHORTAGE":
            suggestions.append(f"放宽知识点“{item.field}”要求，或允许相近知识点补题。")
        elif item.code == "IMAGE_QUESTION_SHORTAGE":
            suggestions.append("降低图片题数量要求，或允许无图片题目补足。")
        elif item.code == "TOTAL_SCORE_MISMATCH":
            suggestions.append("调整总分或各题分值，使总分与题目数量匹配。")
        elif item.code == "REQUIRED_QUESTION_MISSING":
            suggestions.append("取消缺失的指定题目，或重新选择题库中已有题目。")
    return list(dict.fromkeys(suggestions))


def preview_blueprint(session: Session, blueprint_id: str):
    record = session.get(PaperBlueprintRecord, blueprint_id)
    if record is None:
        raise WorkflowNotFoundError("Blueprint not found")
    return compose_paper(session, PaperBlueprint.model_validate(record.blueprint_json))


def create_paper(session: Session, blueprint_id: str) -> SavedPaperRead:
    record = session.get(PaperBlueprintRecord, blueprint_id)
    if record is None:
        raise WorkflowNotFoundError("Blueprint not found")
    if record.status != "confirmed":
        raise BlueprintStateError("只有已确认的蓝图才能组卷")
    blueprint = PaperBlueprint.model_validate(record.blueprint_json)
    preview = compose_paper(session, blueprint)
    if not preview.feasible:
        raise InfeasiblePaperError(_constraint_violations(preview.constraints))
    paper = Paper(
        blueprint_id=record.id,
        version=1,
        status="validating",
        title=blueprint.title,
        total_score=preview.total_score,
        validation_status="pending",
    )
    session.add(paper)
    session.flush()
    paper.root_paper_id = paper.id
    locked = set(blueprint.locked_question_ids)
    for position, item in enumerate(preview.items, 1):
        session.add(PaperItem(
            paper_id=paper.id,
            question_id=item.question_id,
            section=item.question_type,
            position=position,
            score=item.score,
            locked=item.question_id in locked,
        ))
    record.status = "used"
    session.flush()
    report = validate_paper(session, paper.id)
    return _paper_read(session, paper, report)


def replace_paper_item(session: Session, paper_id: str, item_id: str) -> SavedPaperRead:
    source, source_items = _paper_and_records(session, paper_id)
    target = next((item for item in source_items if item.id == item_id), None)
    if target is None:
        raise WorkflowNotFoundError("Paper item not found")
    record = session.get(PaperBlueprintRecord, source.blueprint_id)
    blueprint = PaperBlueprint.model_validate(record.blueprint_json)
    locked_ids = [item.question_id for item in source_items if item.id != item_id]
    replacement_blueprint = blueprint.model_copy(
        update={
            "locked_question_ids": locked_ids,
            "manual_question_ids": [],
            "excluded_question_ids": list(
                dict.fromkeys([*blueprint.excluded_question_ids, target.question_id])
            ),
        }
    )
    preview = compose_paper(session, replacement_blueprint)
    if not preview.feasible:
        raise InfeasiblePaperError(_constraint_violations(preview.constraints))
    old_question_ids = {item.question_id for item in source_items}
    replacement = next(
        (item for item in preview.items if item.question_id not in old_question_ids), None
    )
    if replacement is None:
        raise InfeasiblePaperError(
            [
                ConstraintViolationRead(
                    code="REPLACEMENT_UNAVAILABLE",
                    field=target.section,
                    required=1,
                    actual=0,
                    question_ids=[target.question_id],
                    repairable=True,
                    message="没有可用的替换题目",
                )
            ]
        )
    paper = _new_version(session, source)
    for old in source_items:
        question_id = replacement.question_id if old.id == item_id else old.question_id
        session.add(
            PaperItem(
                paper_id=paper.id,
                question_id=question_id,
                section=old.section,
                position=old.position,
                score=old.score,
                locked=False if old.id == item_id else old.locked,
            )
        )
    session.flush()
    return _finish_version(
        session, paper, source=source, operation_type="replace_question",
        operations=[{"action": "replace_question", "item_id": item_id,
                     "old_question_id": target.question_id,
                     "new_question_id": replacement.question_id}],
    )


def update_paper_item(
    session: Session, paper_id: str, item_id: str, *, score: float | None
) -> SavedPaperRead:
    source, source_items = _paper_and_records(session, paper_id)
    if not any(item.id == item_id for item in source_items):
        raise WorkflowNotFoundError("Paper item not found")
    paper, cloned = _clone_version(session, source, source_items)
    target = next(item for old_id, item in cloned if old_id == item_id)
    if score is not None:
        target.score = score
    session.flush()
    return _finish_version(
        session, paper, source=source, operation_type="update_score",
        operations=[{"action": "update_score", "item_id": item_id, "score": score}],
    )


def lock_paper_item(
    session: Session, paper_id: str, item_id: str, *, locked: bool
) -> SavedPaperRead:
    source, source_items = _paper_and_records(session, paper_id)
    if not any(item.id == item_id for item in source_items):
        raise WorkflowNotFoundError("Paper item not found")
    paper, cloned = _clone_version(session, source, source_items)
    next(item for old_id, item in cloned if old_id == item_id).locked = locked
    session.flush()
    return _finish_version(
        session, paper, source=source, operation_type="lock_question" if locked else "unlock_question",
        operations=[{"action": "lock_question" if locked else "unlock_question", "item_id": item_id}],
    )


def reorder_paper_items(
    session: Session, paper_id: str, item_ids: list[str]
) -> SavedPaperRead:
    source, source_items = _paper_and_records(session, paper_id)
    existing = {item.id for item in source_items}
    if len(item_ids) != len(existing) or set(item_ids) != existing:
        raise BlueprintStateError("item_ids必须包含当前试卷的全部题目且不能重复")

    by_id = {item.id: item for item in source_items}
    original_sections = [item.section for item in source_items]
    requested_sections = [by_id[item_id].section for item_id in item_ids]
    if requested_sections != original_sections:
        raise BlueprintStateError(
            "P0仅支持同题型内部排序，不能跨题型移动题目"
        )
    paper, cloned = _clone_version(session, source, source_items)
    by_old_id = dict(cloned)
    # Use temporary negative positions to avoid the per-paper uniqueness constraint on flush.
    for index, item_id in enumerate(item_ids, 1):
        by_old_id[item_id].position = -index
    session.flush()
    for index, item_id in enumerate(item_ids, 1):
        by_old_id[item_id].position = index
    session.flush()
    return _finish_version(
        session, paper, source=source, operation_type="reorder_questions",
        operations=[{"action": "reorder_questions", "item_ids": item_ids}],
    )


def list_paper_history(session: Session, paper_id: str) -> list[PaperOperationRead]:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise WorkflowNotFoundError("Paper not found")
    root_id = paper.root_paper_id or paper.id
    records = session.scalars(
        select(PaperOperationHistory)
        .where(PaperOperationHistory.root_paper_id == root_id)
        .order_by(PaperOperationHistory.created_at, PaperOperationHistory.id)
    ).all()
    return [_operation_read(record) for record in records]


def undo_paper_operations(
    session: Session, paper_id: str, *, count: int = 1
) -> SavedPaperRead:
    current = session.get(Paper, paper_id)
    if current is None:
        raise WorkflowNotFoundError("Paper not found")
    cursor = current
    operations: list[PaperOperationHistory] = []
    for _ in range(count):
        operation = session.scalar(
            select(PaperOperationHistory).where(
                PaperOperationHistory.result_paper_id == cursor.id
            )
        )
        if operation is None:
            raise BlueprintStateError("没有更多可撤销的试卷操作")
        operations.append(operation)
        source = session.get(Paper, operation.source_paper_id)
        if source is None:
            raise WorkflowNotFoundError("历史版本不存在")
        cursor = source
    target_state = operations[-1].before_state_json
    restored = _restore_snapshot(session, current, target_state)
    return _finish_version(
        session,
        restored,
        source=current,
        operation_type="undo",
        operations=[{"action": "undo_operations", "count": count}],
        undone_operation_id=operations[0].id if count == 1 else None,
    )


def redo_paper_operation(session: Session, paper_id: str) -> SavedPaperRead:
    current = session.get(Paper, paper_id)
    if current is None:
        raise WorkflowNotFoundError("Paper not found")
    undo_record = session.scalar(
        select(PaperOperationHistory).where(
            PaperOperationHistory.result_paper_id == current.id,
            PaperOperationHistory.operation_type == "undo",
        )
    )
    if undo_record is None:
        raise BlueprintStateError("当前版本没有可重做的操作")
    # An undo record's before snapshot is exactly the state that redo must restore,
    # including when the user undid several operations in one turn.
    restored = _restore_snapshot(session, current, undo_record.before_state_json)
    return _finish_version(
        session, restored, source=current, operation_type="redo",
        operations=[{"action": "redo_operation", "undo_operation_id": undo_record.id}],
    )


def restore_paper_version(
    session: Session, paper_id: str, version_id: str
) -> SavedPaperRead:
    current = session.get(Paper, paper_id)
    target = session.get(Paper, version_id)
    if current is None or target is None:
        raise WorkflowNotFoundError("Paper version not found")
    current_root = current.root_paper_id or current.id
    target_root = target.root_paper_id or target.id
    if current_root != target_root:
        raise BlueprintStateError("只能恢复同一试卷的历史版本")
    restored = _restore_snapshot(session, current, _state_snapshot(session, target))
    return _finish_version(
        session, restored, source=current, operation_type="restore_version",
        operations=[{"action": "restore_version", "version_id": version_id}],
    )


def get_paper(session: Session, paper_id: str) -> SavedPaperRead:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise WorkflowNotFoundError("Paper not found")
    report = session.scalar(
        select(ValidationReport)
        .where(ValidationReport.paper_id == paper.id)
        .order_by(ValidationReport.created_at.desc())
    )
    if report is None:
        report_read = validate_paper(session, paper.id)
    else:
        report_read = _report_read(session, report)
    return _paper_read(session, paper, report_read)


def load_paper_preview(session: Session, paper_id: str) -> PaperPreviewRead:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise WorkflowNotFoundError("Paper not found")
    items = _items(session, paper.id)
    return PaperPreviewRead(
        title=paper.title,
        total_score=sum(item.score for item in items),
        items=items,
        constraints=[],
        warnings=[],
        feasible=paper.validation_status == "passed",
    )


def validate_paper(session: Session, paper_id: str) -> ValidationReportRead:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise WorkflowNotFoundError("Paper not found")
    record = session.get(PaperBlueprintRecord, paper.blueprint_id)
    blueprint = PaperBlueprint.model_validate(record.blueprint_json)
    items = _items(session, paper.id)
    violations: list[ConstraintViolationRead] = []
    def add(code, field, required, actual, message, question_ids=None, repairable=True):
        if required != actual:
            violations.append(ConstraintViolationRead(
                code=code, field=field, required=required, actual=actual,
                question_ids=question_ids or [], repairable=repairable, message=message,
            ))
    add("QUESTION_COUNT_MISMATCH", "total_questions", blueprint.total_questions, len(items), "题目总数不符")
    add("TOTAL_SCORE_MISMATCH", "total_score", blueprint.total_score, sum(x.score for x in items), "试卷总分不符")
    counts = Counter(item.question_type for item in items)
    for question_type, required in blueprint.question_type_counts.items():
        add("QUESTION_TYPE_COUNT_MISMATCH", question_type, required, counts[question_type], f"{question_type}数量不符")
    for section in blueprint.sections:
        actual = sum(item.score for item in items if item.question_type == section.question_type)
        add("SECTION_SCORE_MISMATCH", section.question_type, section.total_score, actual, f"{section.question_type}部分分值不符")
    knowledge = Counter(name for item in items for name in item.knowledge)
    for quota in blueprint.knowledge_quotas:
        if knowledge[quota.name] < quota.count:
            violations.append(ConstraintViolationRead(code="KNOWLEDGE_SHORTAGE", field=quota.name, required=quota.count, actual=knowledge[quota.name], question_ids=[], repairable=True, message=f"{quota.name}覆盖不足"))

    metadata = (record.blueprint_json or {}).get("_agent_metadata") or {}
    generation_values = {
        key: value
        for key, value in metadata.items()
        if key in GenerationConstraints.model_fields
    }
    generation_constraints = GenerationConstraints.model_validate(
        generation_values
    )
    if generation_constraints.target_duration_min is not None:
        missing_profiles = [
            item.question_id
            for item in items
            if item.estimated_time_min is None
        ]
        if missing_profiles:
            violations.append(
                ConstraintViolationRead(
                    code="DURATION_PROFILE_MISSING",
                    field="estimated_duration_min",
                    required="approved_question_profile",
                    actual=len(missing_profiles),
                    question_ids=missing_profiles,
                    repairable=True,
                    message="部分题目缺少已审核的预计作答时长",
                )
            )
        else:
            target = generation_constraints.target_duration_min
            tolerance = generation_constraints.duration_tolerance_min
            lower = max(1, target - tolerance)
            upper = target + tolerance
            actual_duration = sum(
                item.estimated_time_min or 0
                for item in items
            )
            if not lower <= actual_duration <= upper:
                violations.append(
                    ConstraintViolationRead(
                        code="DURATION_OUT_OF_RANGE",
                        field="estimated_duration_min",
                        required=f"{lower}-{upper}",
                        actual=actual_duration,
                        question_ids=[],
                        repairable=True,
                        message="试卷预计作答时长超出教学设计允许范围",
                    )
                )

    ids = [item.question_id for item in items]
    duplicates = [question_id for question_id, count in Counter(ids).items() if count > 1]
    add("DUPLICATE_QUESTION", "question_id", 0, len(duplicates), "试卷包含重复题目", duplicates)
    missing_solutions = [item.question_id for item in items if not item.solution_steps]
    add("SOLUTION_MISSING", "solution", 0, len(missing_solutions), "题目缺少解析", missing_solutions)
    excluded = [item.question_id for item in items if item.question_id in blueprint.excluded_question_ids]
    add("EXCLUDED_QUESTION_INCLUDED", "excluded_question_ids", 0, len(excluded), "包含已排除题目", excluded)
    excluded_topics = [item.question_id for item in items if set(item.knowledge).intersection(blueprint.excluded_topics)]
    add("EXCLUDED_TOPIC_INCLUDED", "excluded_topics", 0, len(excluded_topics), "包含已排除知识点", excluded_topics)
    report = ValidationReport(paper_id=paper.id, passed=not violations)
    session.add(report)
    session.flush()
    for violation in violations:
        session.add(ConstraintViolation(
            report_id=report.id, code=violation.code, field=violation.field,
            required_json=violation.required, actual_json=violation.actual,
            question_ids_json=violation.question_ids, repairable=violation.repairable,
            message=violation.message,
        ))
    paper.status = "passed" if report.passed else "failed"
    paper.validation_status = "passed" if report.passed else "failed"
    session.flush()
    return ValidationReportRead(id=report.id, paper_id=paper.id, passed=report.passed, violations=violations, created_at=report.created_at)


def _items(
    session: Session,
    paper_id: str,
) -> list[PaperItemRead]:
    records = session.scalars(
        select(PaperItem)
        .where(PaperItem.paper_id == paper_id)
        .order_by(PaperItem.position)
    ).all()

    result = []
    for item in records:
        question = session.get(Question, item.question_id)
        knowledge = list(
            session.scalars(
                select(KnowledgeNode.name)
                .join(
                    QuestionKnowledgeLink,
                    QuestionKnowledgeLink.knowledge_node_id
                    == KnowledgeNode.id,
                )
                .where(
                    QuestionKnowledgeLink.question_id
                    == question.id
                )
            ).all()
        )
        draft = session.get(
            QuestionDraft,
            question.draft_id,
        )
        profile = session.scalar(
            select(QuestionProfile)
            .where(
                QuestionProfile.question_id == question.id,
                QuestionProfile.profile_status == "approved",
            )
            .order_by(
                QuestionProfile.profile_version.desc()
            )
            .limit(1)
        )

        result.append(
            PaperItemRead(
                item_id=item.id,
                question_id=question.id,
                question_text=question.question_text,
                question_type=item.section,
                score=item.score,
                knowledge=knowledge,
                final_answer=question.final_answer,
                solution_steps=(
                    question.solution_json or {}
                ).get("solution_steps", []),
                has_image=bool(
                    draft and draft.image_path
                ),
                locked=item.locked,
                difficulty=(
                    profile.difficulty
                    if profile is not None else None
                ),
                estimated_time_min=(
                    profile.estimated_time_min
                    if profile is not None else None
                ),
                reasoning_depth=(
                    profile.reasoning_depth
                    if profile is not None else None
                ),
                calculation_load=(
                    profile.calculation_load
                    if profile is not None else None
                ),
                knowledge_depth=(
                    profile.knowledge_depth
                    if profile is not None else None
                ),
                comprehensive_level=(
                    profile.comprehensive_level
                    if profile is not None else None
                ),
            )
        )
    return result

def _constraint_violations(checks: list[ConstraintCheck]) -> list[ConstraintViolationRead]:
    codes = {"题目总数": "QUESTION_COUNT_SHORTAGE", "试卷总分": "TOTAL_SCORE_MISMATCH", "指定题目": "REQUIRED_QUESTION_MISSING", "图片题数量": "IMAGE_QUESTION_SHORTAGE", "预计时长": "DURATION_OUT_OF_RANGE"}
    result = []
    for check in checks:
        if check.satisfied:
            continue
        field = check.name.split("：", 1)[-1]
        code = "QUESTION_TYPE_SHORTAGE" if check.name.startswith("题型：") else "KNOWLEDGE_SHORTAGE" if check.name.startswith("知识点：") else codes.get(check.name, "CONSTRAINT_UNSATISFIED")
        result.append(ConstraintViolationRead(code=code, field=field, required=check.required, actual=check.actual, question_ids=[], repairable=True, message=f"{check.name}无法满足"))
    return result


def _blueprint_read(record: PaperBlueprintRecord) -> BlueprintCreateRead:
    return BlueprintCreateRead(blueprint_id=record.id, status=record.status, blueprint=PaperBlueprint.model_validate(record.blueprint_json))


def _report_read(session: Session, report: ValidationReport) -> ValidationReportRead:
    values = session.scalars(select(ConstraintViolation).where(ConstraintViolation.report_id == report.id)).all()
    return ValidationReportRead(id=report.id, paper_id=report.paper_id, passed=report.passed, created_at=report.created_at, violations=[ConstraintViolationRead(code=x.code, field=x.field, required=x.required_json, actual=x.actual_json, question_ids=x.question_ids_json, repairable=x.repairable, message=x.message) for x in values])


def _paper_read(session: Session, paper: Paper, report: ValidationReportRead) -> SavedPaperRead:
    return SavedPaperRead(
        paper_id=paper.id,
        blueprint_id=paper.blueprint_id,
        root_paper_id=paper.root_paper_id or paper.id,
        parent_version_id=paper.parent_version_id,
        teaching_design_version_id=paper.teaching_design_version_id,
        version=paper.version,
        status=paper.status,
        total_score=paper.total_score,
        validation_status=paper.validation_status,
        preview=load_paper_preview(session, paper.id),
        validation_report=report,
        created_at=paper.created_at,
    )


def _paper_and_records(session: Session, paper_id: str) -> tuple[Paper, list[PaperItem]]:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise WorkflowNotFoundError("Paper not found")
    items = list(
        session.scalars(
            select(PaperItem).where(PaperItem.paper_id == paper.id).order_by(PaperItem.position)
        ).all()
    )
    return paper, items


def _new_version(session: Session, source: Paper) -> Paper:
    root_id = source.root_paper_id or source.id
    version = session.scalar(
        select(func.max(Paper.version)).where(Paper.root_paper_id == root_id)
    ) or source.version
    paper = Paper(
        blueprint_id=source.blueprint_id,
        root_paper_id=root_id,
        parent_version_id=source.id,
        version=version + 1,
        status="validating",
        title=source.title,
        total_score=source.total_score,
        teaching_design_version_id=source.teaching_design_version_id,
        validation_status="pending",
    )
    session.add(paper)
    session.flush()
    return paper


def _clone_version(
    session: Session, source: Paper, items: list[PaperItem]
) -> tuple[Paper, list[tuple[str, PaperItem]]]:
    paper = _new_version(session, source)
    cloned = []
    for item in items:
        copy = PaperItem(
            paper_id=paper.id,
            question_id=item.question_id,
            section=item.section,
            position=item.position,
            score=item.score,
            locked=item.locked,
        )
        session.add(copy)
        cloned.append((item.id, copy))
    session.flush()
    return paper, cloned


def _state_snapshot(session: Session, paper: Paper) -> dict:
    records = session.scalars(
        select(PaperItem)
        .where(PaperItem.paper_id == paper.id)
        .order_by(PaperItem.position)
    ).all()
    return {
        "paper_id": paper.id,
        "blueprint_id": paper.blueprint_id,
        "teaching_design_version_id": paper.teaching_design_version_id,
        "title": paper.title,
        "total_score": paper.total_score,
        "items": [
            {
                "question_id": item.question_id,
                "section": item.section,
                "position": item.position,
                "score": item.score,
                "locked": item.locked,
            }
            for item in records
        ],
    }


def _restore_snapshot(session: Session, source: Paper, snapshot: dict) -> Paper:
    paper = _new_version(session, source)
    paper.blueprint_id = snapshot["blueprint_id"]
    paper.title = snapshot["title"]
    paper.total_score = snapshot["total_score"]
    for item in snapshot["items"]:
        session.add(
            PaperItem(
                paper_id=paper.id,
                question_id=item["question_id"],
                section=item["section"],
                position=item["position"],
                score=item["score"],
                locked=item["locked"],
            )
        )
    session.flush()
    return paper


def _operation_read(record: PaperOperationHistory) -> PaperOperationRead:
    return PaperOperationRead(
        operation_id=record.id,
        root_paper_id=record.root_paper_id,
        source_paper_id=record.source_paper_id,
        result_paper_id=record.result_paper_id,
        operation_type=record.operation_type,
        operations=record.operations_json,
        undone_operation_id=record.undone_operation_id,
        created_at=record.created_at,
    )


def _finish_version(
    session: Session,
    paper: Paper,
    *,
    source: Paper | None = None,
    operation_type: str | None = None,
    operations: list[dict] | None = None,
    undone_operation_id: str | None = None,
) -> SavedPaperRead:
    report = validate_paper(session, paper.id)
    if source is not None and operation_type is not None:
        session.add(
            PaperOperationHistory(
                root_paper_id=paper.root_paper_id or paper.id,
                source_paper_id=source.id,
                result_paper_id=paper.id,
                operation_type=operation_type,
                operations_json=operations or [],
                before_state_json=_state_snapshot(session, source),
                after_state_json=_state_snapshot(session, paper),
                undone_operation_id=undone_operation_id,
            )
        )
        session.flush()
    return _paper_read(session, paper, report)

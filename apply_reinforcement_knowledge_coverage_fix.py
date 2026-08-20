#!/usr/bin/env python3
"""
Apply the cross-chapter reinforcement knowledge-coverage fix.

Run from the calculus_knowledge_agent repository root.

Business rule:
- Wrong-question feedback is evidence, not mastery diagnosis.
- Collect every current-taxonomy knowledge point linked to the reported wrong questions.
- Deduplicate knowledge points.
- Each unique knowledge point becomes a hard coverage target: min 1 question.
- Reinforcement scope is the union of those knowledge points' real chapters.
- evidence_count / weight remain observable compatibility fields only; generation
  no longer uses weight to infer how many questions to generate.
- Reuse existing required_knowledge_names -> PaperBlueprint.knowledge_quotas.
- Preserve replace_existing_pending=True for fresh-task isolation.

Safety:
- All three patched files are transformed and syntax-checked in memory first.
- Nothing is written if any expected source anchor is missing.
- Backups are created with suffix .bak.reinforcement_coverage.
"""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path.cwd()
REINFORCEMENT = ROOT / "src/calculus_agent/agent/services/reinforcement.py"
PAPER_TOOLS = ROOT / "src/calculus_agent/agent/tools/paper_tools.py"
TESTS = ROOT / "tests/teacher_agent/test_reinforcement_plan.py"


def die(message: str) -> None:
    raise RuntimeError(message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        die(f"[{label}] expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_between(
    text: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
    label: str,
) -> str:
    start = text.find(start_marker)
    if start < 0:
        die(f"[{label}] start marker not found")
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        die(f"[{label}] end marker not found")
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def transformed_reinforcement(text: str) -> str:
    isolated_preview = """        preview = self.generation_service.preview(
            patch,
            replace_existing_pending=True,
        )
"""
    old_preview = "        preview = self.generation_service.preview(patch)\n"

    if isolated_preview not in text:
        if old_preview not in text:
            die("[reinforcement pending isolation] preview anchor not found")
        text = text.replace(old_preview, isolated_preview, 1)

    build_context = r"""    def build_context(
        self,
        paper_id: str,
        items: list[Any],
    ) -> ReinforcementContext:
        paper = self.session.get(Paper, paper_id)
        if paper is None:
            raise ReinforcementError(
                "no_current_paper",
                "当前没有可操作的试卷版本，无法根据错题生成巩固卷。",
            )

        paper_items = list(
            self.session.scalars(
                select(PaperItem)
                .where(PaperItem.paper_id == paper_id)
                .order_by(PaperItem.position)
            )
        )
        if not paper_items:
            raise ReinforcementError(
                "no_current_paper",
                "当前试卷没有题目，无法根据错题生成巩固卷。",
            )

        section_orders = section_order_map(paper_items)
        items_by_position = {item.position: item for item in paper_items}

        current_nodes = list(
            current_taxonomy_knowledge_nodes(self.session)
        )
        valid_knowledge_ids = {
            node.id for node in current_nodes
        }
        nodes_by_id = {
            node.id: node for node in current_nodes
        }
        curriculum_by_id = {
            node.id: node
            for node in self.session.scalars(
                select(CurriculumNode)
            )
        }

        # Resolve the entire teacher feedback set first.  This preserves
        # all-or-nothing behavior: no PendingGeneration is touched if any
        # address is invalid.
        resolved_items: list[tuple[PaperItem, Any]] = []
        seen_item_ids: set[str] = set()
        warnings: list[str] = []

        for feedback in items:
            item = self._resolve_item(
                feedback,
                paper_items,
                items_by_position,
            )
            if item is None:
                raise ReinforcementError(
                    "feedback_question_not_found",
                    self._not_found_message(feedback),
                )
            if item.id in seen_item_ids:
                warnings.append(
                    "duplicate_feedback_reference_ignored"
                )
                continue
            seen_item_ids.add(item.id)
            resolved_items.append((item, feedback))

        evidence: list[ReinforcementEvidence] = []
        source_question_ids: list[str] = []

        # Observable evidence only.  It no longer controls generation weight.
        counts: Counter[str] = Counter()

        for item, feedback in resolved_items:
            question = self.session.get(
                Question,
                item.question_id,
            )
            if question is None:
                raise ReinforcementError(
                    "feedback_question_not_found",
                    "当前试卷题目缺少对应的题库题目记录，无法解析知识点。",
                )

            # Keep authoritative Question chapter ownership as a data-integrity
            # invariant, but do NOT use it as the reinforcement scope.
            if self._chapter_title(
                question.curriculum_chapter_id,
                curriculum_by_id,
            ) is None:
                raise ReinforcementError(
                    "reinforcement_scope_unresolved",
                    "反馈的题目无法确定其所属章节，无法据此生成巩固卷。",
                )

            knowledge_ids = self._valid_knowledge_ids(
                question.id,
                valid_knowledge_ids,
            )
            if not knowledge_ids:
                raise ReinforcementError(
                    "reinforcement_knowledge_unresolved",
                    "反馈的题目在当前知识点分类中没有可用知识点，"
                    "无法据此确定强化重点。",
                )

            # _valid_knowledge_ids already deduplicates per Question, so one
            # Question contributes at most one evidence count to one Knowledge.
            for knowledge_id in knowledge_ids:
                counts[knowledge_id] += 1

            if question.id not in source_question_ids:
                source_question_ids.append(question.id)

            evidence.append(
                ReinforcementEvidence(
                    paper_item_id=item.id,
                    question_id=question.id,
                    position=item.position,
                    section_type=item.section,
                    section_order=section_orders.get(
                        item.id,
                        item.position,
                    ),
                    question_type=question.question_type,
                    difficulty=self._difficulty(question.id),
                    knowledge=[
                        {
                            "knowledge_node_id": knowledge_id,
                            "knowledge_name": nodes_by_id[
                                knowledge_id
                            ].name,
                        }
                        for knowledge_id in knowledge_ids
                    ],
                    teacher_note=feedback.teacher_note,
                )
            )

        targets = [
            KnowledgeReinforcementTarget(
                knowledge_node_id=knowledge_id,
                knowledge_name=nodes_by_id[
                    knowledge_id
                ].name,
                evidence_count=evidence_count,
                # Compatibility / observability only.
                # compile_patch() no longer consumes this weight.
                weight=reinforcement_weight(
                    evidence_count
                ),
            )
            for knowledge_id, evidence_count
            in counts.items()
        ]
        targets.sort(
            key=lambda target: (
                -target.evidence_count,
                target.knowledge_name,
                target.knowledge_node_id,
            )
        )

        if not targets:
            raise ReinforcementError(
                "reinforcement_knowledge_unresolved",
                "反馈的题目没有可用于巩固的知识点。",
            )

        # A later-chapter question may legitimately use earlier knowledge.
        # Reinforcement therefore spans the real chapters of ALL target
        # KnowledgeNodes rather than forcing every target into the source
        # Question's owning chapter.
        chapter_refs: dict[str, str] = {}
        for target in targets:
            knowledge_node = nodes_by_id[
                target.knowledge_node_id
            ]
            chapter_ref = self._chapter_ref(
                knowledge_node.curriculum_node_id,
                curriculum_by_id,
            )
            if chapter_ref is None:
                raise ReinforcementError(
                    "reinforcement_scope_unresolved",
                    f"知识点“{target.knowledge_name}”"
                    "没有可确定的章节归属，无法生成巩固卷。",
                )

            chapter_id, chapter_title = chapter_ref
            chapter_refs[chapter_id] = chapter_title

        ordered_chapter_ids = sorted(
            chapter_refs,
            key=lambda chapter_id: (
                getattr(
                    curriculum_by_id[chapter_id],
                    "sort_order",
                    0,
                )
                or 0,
                chapter_refs[chapter_id],
                chapter_id,
            ),
        )

        return ReinforcementContext(
            source_paper_id=paper_id,
            source_question_ids=source_question_ids,
            evidence=evidence,
            target_knowledge=targets,
            scope_names=[
                chapter_refs[chapter_id]
                for chapter_id in ordered_chapter_ids
            ],
            scope_chapter_ids=ordered_chapter_ids,
            warnings=warnings,
        )"""

    compile_patch = r"""    def compile_patch(
        self,
        context: ReinforcementContext,
    ) -> GenerationPlanPatch:
        target_names = list(
            dict.fromkeys(
                target.knowledge_name
                for target in context.target_knowledge
            )
        )

        if not target_names:
            raise ReinforcementError(
                "reinforcement_knowledge_unresolved",
                "没有可用于巩固的知识点。",
            )

        if len(target_names) > 100:
            raise ReinforcementError(
                "reinforcement_target_limit_exceeded",
                "本次错题涉及的知识点超过100个，请缩小反馈范围后重试。",
            )

        # V1 executable rule:
        # unique wrong-question knowledge -> min 1 selected question per target.
        #
        # knowledge_preferences keeps the targets visible as a soft ordering
        # signal. required_knowledge_names is the hard generation contract.
        # No evidence_count -> weight -> selector inference is used here.
        return GenerationPlanPatch(
            paper_type="chapter_exercise",
            scope_names=list(context.scope_names),
            question_count=len(target_names),
            knowledge_preferences=target_names,
            required_knowledge_names=target_names,
        )"""

    text = replace_between(
        text,
        "    def build_context(",
        "    def compile_patch(",
        build_context,
        "ReinforcementService.build_context",
    )

    text = replace_between(
        text,
        "    def compile_patch(",
        "    def _resolve_item(",
        compile_patch,
        "ReinforcementService.compile_patch",
    )

    if "    def _chapter_ref(" not in text:
        helper = r"""    def _chapter_ref(
        self,
        curriculum_node_id: str | None,
        curriculum_by_id: dict[str, CurriculumNode],
    ) -> tuple[str, str] | None:
        'Resolve any curriculum node to its owning chapter id + title.'
        current = curriculum_node_id

        while current is not None:
            node = curriculum_by_id.get(current)
            if node is None:
                return None
            if node.node_type == "chapter":
                return node.id, node.title
            current = node.parent_id

        return None

"""
        marker = "    def _chapter_title(\n"
        if marker not in text:
            die(
                "[reinforcement chapter helper] "
                "_chapter_title marker not found"
            )
        text = text.replace(
            marker,
            helper + marker,
            1,
        )

    return text


def transformed_paper_tools(text: str) -> str:
    # Use validated KnowledgeQuota instances; model_copy(update=...) does not
    # recursively validate raw nested dicts.
    if "    KnowledgeQuota,\n" not in text:
        text = replace_once(
            text,
            """from calculus_agent.schemas import (
    PaperBlueprint,
""",
            """from calculus_agent.schemas import (
    KnowledgeQuota,
    PaperBlueprint,
""",
            "paper_tools KnowledgeQuota import",
        )

    if "        required_ids,\n        required_errors,\n" not in text:
        text = replace_once(
            text,
            """    if knowledge_errors:
        return (
            None,
            [],
            knowledge_errors,
            knowledge_questions,
        )

    warnings: list[str] = []
""",
            """    if knowledge_errors:
        return (
            None,
            [],
            knowledge_errors,
            knowledge_questions,
        )

    (
        required_names,
        required_ids,
        required_errors,
        required_questions,
    ) = _knowledge_preferences(
        session,
        request.required_knowledge_names or [],
        scope_ids,
        scopes,
    )

    if required_errors:
        return (
            None,
            [],
            required_errors,
            required_questions,
        )

    warnings: list[str] = []
""",
            "resolve required_knowledge_names",
        )

    if "    if required_names and not requirements:\n" not in text:
        text = replace_once(
            text,
            """    if requirements:
        canonical = [
""",
            """    if required_names and not requirements:
        # Generic hard-knowledge-coverage plan.  A trusted caller can specify
        # required_knowledge_names without inventing a question-type split.
        required_count = len(required_names)
        desired_count = (
            request.question_count
            if request.question_count is not None
            else required_count
        )

        if desired_count < required_count:
            return (
                None,
                [],
                ["question_count_below_required_knowledge_coverage"],
                [
                    f"当前要求至少覆盖{required_count}个知识点，"
                    f"但总题数只有{desired_count}题。"
                    "请增加题量或减少必覆盖知识点。"
                ],
            )

        default_total_score = max(
            desired_count,
            min(100, desired_count * 10),
        )

        blueprint = PaperBlueprint(
            title=(
                f"{scopes[0] if len(scopes) == 1 else '高等数学'}"
                "专项巩固练习"
            ),
            total_questions=desired_count,
            total_score=(
                request.total_score
                or default_total_score
            ),
            soft_knowledge_preferences=list(
                dict.fromkeys(
                    [
                        *preferred_names,
                        *required_names,
                    ]
                )
            ),
            # New papers receive the actual fresh seed only at execution.
            seed=None,
        )

    elif requirements:
        canonical = [
""",
            "required knowledge flexible blueprint",
        )

    if (
        "    # Hard required-knowledge coverage compiled deterministically.\n"
        not in text
    ):
        text = replace_once(
            text,
            """    (
        allowed,
        preferred,
        fallback,
    ) = _difficulty(
""",
            """    # Hard required-knowledge coverage compiled deterministically.
    # The existing selector / CP-SAT path already enforces knowledge_quotas.
    if required_names:
        if blueprint.total_questions < len(required_names):
            return (
                None,
                warnings,
                ["question_count_below_required_knowledge_coverage"],
                [
                    f"当前要求至少覆盖{len(required_names)}个知识点，"
                    f"但蓝图只有{blueprint.total_questions}题。"
                    "请增加题量或减少必覆盖知识点。"
                ],
            )

        blueprint = blueprint.model_copy(
            update={
                "knowledge_quotas": [
                    KnowledgeQuota(
                        name=name,
                        count=1,
                    )
                    for name in required_names
                ],
                "strict_knowledge": True,
                "soft_knowledge_preferences": list(
                    dict.fromkeys(
                        [
                            *preferred_names,
                            *required_names,
                        ]
                    )
                ),
            }
        )

    (
        allowed,
        preferred,
        fallback,
    ) = _difficulty(
""",
            "compile hard knowledge quotas",
        )


    return text


def transformed_tests(text: str) -> str:
    test_name = (
        "test_cross_chapter_wrong_question_expands_scope_"
        "and_compiles_hard_knowledge_coverage"
    )
    if test_name in text:
        return text

    addition = r"""

def test_cross_chapter_wrong_question_expands_scope_and_compiles_hard_knowledge_coverage(session):
    # A later-chapter question may legitimately depend on earlier knowledge.
    # Reinforcement must practice every linked current knowledge point instead
    # of asking the teacher to choose one chapter.
    tb = _textbook(session)
    c1 = _chapter(
        session,
        tb,
        "c1",
        "第一章 函数与极限",
    )
    c3 = _chapter(
        session,
        tb,
        "c3",
        "第三章 微分中值定理与导数的应用",
    )

    prerequisite = _knode(
        session,
        "k-prerequisite",
        c1.id,
        "函数的求导法则",
    )
    target = _knode(
        session,
        "k-target",
        c3.id,
        "函数的单调性与曲线的凹凸性",
    )

    paper = _paper(
        session,
        "paper-cross-chapter-reinforcement",
        "第三章测试卷",
    )
    question = _question(
        session,
        "q-cross-chapter",
        "计算题",
        c3.id,
        [
            prerequisite.id,
            target.id,
        ],
    )
    _item(
        session,
        paper,
        question,
        1,
    )

    conversation_id = "conv-cross-chapter-reinforcement"
    service = _service(
        session,
        conversation_id,
    )

    result = service.prepare(
        paper.id,
        [
            _feedback(
                address=QuestionAddress(
                    section_type="计算题",
                    section_order=1,
                )
            )
        ],
    )

    assert result.preview.ok is True
    assert result.preview.blocking_errors == []
    assert result.preview.clarification_questions == []

    # Scope is the union of the target knowledge chapters.
    assert set(result.context.scope_names) == {
        "第一章 函数与极限",
        "第三章 微分中值定理与导数的应用",
    }

    # Every unique linked knowledge point becomes one hard coverage target.
    assert set(
        result.patch.required_knowledge_names or []
    ) == {
        "函数的求导法则",
        "函数的单调性与曲线的凹凸性",
    }
    assert result.patch.question_count == 2

    # Legacy evidence weight is not an executable generation constraint.
    assert result.patch.knowledge_priority_weights is None

    pending = DatabasePendingReplacementStore(
        session
    ).get_generation(conversation_id)
    assert pending is not None
    assert pending.request.question_count == 2
    assert set(
        pending.request.required_knowledge_names or []
    ) == {
        "函数的求导法则",
        "函数的单调性与曲线的凹凸性",
    }

    # Verify the executable blueprint, not merely the semantic patch.
    from calculus_agent.agent.tools.paper_tools import (
        build_structured_generation_request,
    )

    (
        generation_request,
        _,
        errors,
        questions,
    ) = build_structured_generation_request(
        session,
        pending.request,
    )

    assert errors == []
    assert questions == []
    assert generation_request is not None
    assert (
        generation_request.blueprint.total_questions
        == 2
    )
    assert (
        generation_request.blueprint.strict_knowledge
        is True
    )
    assert {
        quota.name: quota.count
        for quota in (
            generation_request
            .blueprint
            .knowledge_quotas
        )
    } == {
        "函数的求导法则": 1,
        "函数的单调性与曲线的凹凸性": 1,
    }
"""

    return text.rstrip() + "\n" + addition + "\n"


def backup(path: Path) -> None:
    backup_path = path.with_suffix(
        path.suffix + ".bak.reinforcement_coverage"
    )
    if not backup_path.exists():
        shutil.copy2(
            path,
            backup_path,
        )


def main() -> int:
    for path in (
        REINFORCEMENT,
        PAPER_TOOLS,
        TESTS,
    ):
        if not path.is_file():
            die(
                f"missing {path}. "
                "请从 calculus_knowledge_agent 仓库根目录运行。"
            )

    originals = {
        REINFORCEMENT: REINFORCEMENT.read_text(
            encoding="utf-8"
        ),
        PAPER_TOOLS: PAPER_TOOLS.read_text(
            encoding="utf-8"
        ),
        TESTS: TESTS.read_text(
            encoding="utf-8"
        ),
    }

    # Transform everything in memory first.
    updated = {
        REINFORCEMENT: transformed_reinforcement(
            originals[REINFORCEMENT]
        ),
        PAPER_TOOLS: transformed_paper_tools(
            originals[PAPER_TOOLS]
        ),
        TESTS: transformed_tests(
            originals[TESTS]
        ),
    }

    # Syntax-check every resulting Python file BEFORE any write.
    for path, content in updated.items():
        compile(
            content,
            str(path),
            "exec",
        )

    changed = [
        path
        for path in updated
        if updated[path] != originals[path]
    ]

    # Only now modify the working tree.
    for path in changed:
        backup(path)

    for path in changed:
        path.write_text(
            updated[path],
            encoding="utf-8",
        )
        print(f"[patched] {path}")

    for path in updated:
        if path not in changed:
            print(f"[unchanged] {path}")

    print()
    print("Reinforcement knowledge-coverage fix applied.")
    print(
        "Backups: *.bak.reinforcement_coverage"
    )
    print()
    print("Run:")
    print(
        "  uv run pytest -q "
        "tests/teacher_agent/test_reinforcement_plan.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

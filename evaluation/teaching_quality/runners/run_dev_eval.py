"""Capture real Tutor responses for frozen Teaching Quality Dev cases.

No Blind cases are loaded/executed. Historical calibration files are not modified.
A case is scored only when every required structured observation is available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
REPORTS = REPO / "evaluation/artifacts/reports/teaching_quality/runs"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_dev_cases() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = yaml.safe_load((ROOT / "datasets/manifests/split_v1.yaml").read_text(encoding="utf-8"))
    if manifest["status"] != "frozen" or manifest["splits"]["blind"]["status"] != "frozen_do_not_run":
        raise ValueError("Dev/Blind split is not frozen")
    actual_hashes = []
    by_id: dict[str, dict[str, Any]] = {}
    for record in manifest["dataset"]["files"]:
        path = REPO / record["path"]
        digest = sha(path)
        if digest != record["sha256"]:
            raise ValueError(f"frozen case file changed: {record['path']}")
        actual_hashes.append(f"{record['path']}:{digest}\n")
        for case in yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]:
            if case["case_id"] in by_id:
                raise ValueError(f"duplicate case ID: {case['case_id']}")
            by_id[case["case_id"]] = case
    if hashlib.sha256("".join(actual_hashes).encode()).hexdigest() != manifest["dataset"]["sha256"]:
        raise ValueError("dataset aggregate hash mismatch")
    dev_entries = manifest["splits"]["dev"]["cases"]
    blind_ids = {row["case_id"] for row in manifest["splits"]["blind"]["cases"]}
    if len(dev_entries) != 24 or len(blind_ids) != 12:
        raise ValueError("expected 24 Dev and 12 Blind cases")
    dev_ids = {row["case_id"] for row in dev_entries}
    if dev_ids & blind_ids or len(dev_ids) != 24:
        raise ValueError("Dev/Blind overlap or duplicate")
    selected = []
    for entry in dev_entries:
        case = by_id[entry["case_id"]]
        if (case["family_id"], case["suite"]) != (entry["family_id"], entry["suite"]):
            raise ValueError(f"manifest metadata mismatch: {entry['case_id']}")
        if case["human_review"]["status"] != "approved":
            raise ValueError(f"Dev case not approved: {entry['case_id']}")
        selected.append(case)
    return selected, manifest


def source_database(settings: Any) -> Path | None:
    url = settings.database_url
    if not url.startswith("sqlite:///") or url == "sqlite:///:memory:":
        raise ValueError("For isolation, only file-based SQLite is supported")
    source = Path(url.removeprefix("sqlite:///"))
    if not source.is_absolute():
        source = REPO / source
    return source.resolve() if source.is_file() else None


def make_case_db(source: Path | None, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source is None:
        return
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
        with sqlite3.connect(dest) as target:
            original.backup(target)


def normalized_output(result: Any, session: Any) -> dict[str, Any]:
    actual: dict[str, Any] = {"response": result.message}
    if result.teaching_design is not None:
        actual["teaching_design"] = result.teaching_design.content.model_dump(mode="json")
    elif result.teaching_planning_draft is not None:
        actual["teaching_planning_draft"] = result.teaching_planning_draft.model_dump(mode="json")
    if result.paper is not None and result.paper.ok and result.paper.paper_id:
        from sqlalchemy import select
        from calculus_agent.models import (
            CurriculumNode, KnowledgeNode, Paper, PaperItem, Question,
            QuestionKnowledgeLink, QuestionProfile,
        )

        paper = session.get(Paper, str(result.paper.paper_id))
        if paper is not None:
            rows = session.execute(
                select(PaperItem, Question)
                .join(Question, PaperItem.question_id == Question.id)
                .where(PaperItem.paper_id == paper.id)
                .order_by(PaperItem.position)
            ).all()
            question_ids = [question.id for _, question in rows]
            profiles = session.scalars(
                select(QuestionProfile)
                .where(QuestionProfile.question_id.in_(question_ids), QuestionProfile.profile_status == "approved")
                .order_by(QuestionProfile.question_id, QuestionProfile.profile_version.desc())
            ).all()
            profile_by_question = {}
            for profile in profiles:
                profile_by_question.setdefault(profile.question_id, profile)
            knowledge_by_question: dict[str, list[dict[str, str]]] = {question_id: [] for question_id in question_ids}
            for question_id, node_id, name in session.execute(
                select(QuestionKnowledgeLink.question_id, KnowledgeNode.id, KnowledgeNode.name)
                .join(KnowledgeNode, KnowledgeNode.id == QuestionKnowledgeLink.knowledge_node_id)
                .where(QuestionKnowledgeLink.question_id.in_(question_ids))
            ):
                knowledge_by_question[question_id].append({"id": node_id, "name": name})
            questions = []
            for item, question in rows:
                chapter = session.get(CurriculumNode, question.curriculum_chapter_id) if question.curriculum_chapter_id else None
                profile = profile_by_question.get(question.id)
                questions.append({
                    "id": question.id,
                    "textbook_id": chapter.textbook_id if chapter else None,
                    "chapter_id": question.curriculum_chapter_id,
                    "question_type": question.question_type,
                    "score": item.score,
                    "chapter": f"第{chapter.code}章" if chapter and chapter.code else (chapter.title if chapter else None),
                    "chapter_title": chapter.title if chapter else None,
                    "difficulty": profile.difficulty if profile else question.difficulty,
                    "difficulty_source": "approved_question_profile" if profile else "question",
                    "knowledge_points": knowledge_by_question[question.id],
                    "question_text": question.question_text,
                })
            actual["paper"] = {
                "id": paper.id,
                "total_score": paper.total_score,
                "questions": questions,
                "question_type_counts": dict(Counter(q["question_type"] for q in questions)),
            }
    return actual


def grade_result(
    case: dict[str, Any], actual: dict[str, Any], adapter: Any, *, judge_version: str = "v1",
) -> dict[str, Any]:
    from evaluation.teaching_quality.evaluators.behavioral import (
        judge_prompt_artifacts, parse_case_judge_output,
    )
    from evaluation.teaching_quality.evaluators.contract_validator import (
        audit_judge_contract, canonical_actual_counts,
    )
    from evaluation.teaching_quality.evaluators.scoring import score_case
    from evaluation.teaching_quality.evaluators.semantic import ModelBackedSemanticAssertionEvaluator
    from evaluation.teaching_quality.evaluators.structural import StructuralAssertionGrader
    from evaluation.teaching_quality.runners.dev_contract import adapt_dev_observation
    from evaluation.teaching_quality.runners.scope_review import assess_scope

    semantic = ModelBackedSemanticAssertionEvaluator(adapter)
    structural_grader = StructuralAssertionGrader(semantic_evaluator=semantic)
    observations = []
    unknown = []
    adapted_fields = {}
    for assertion in case["expected"]["structural_assertions"]:
        adapted_assertion, observed_actual, provenance = adapt_dev_observation(assertion, actual)
        if provenance:
            adapted_fields[assertion["id"]] = provenance
        if adapted_assertion.get("type") == "allowed_scope_only" and adapted_assertion.get("target") == "teaching_design.topics":
            forbidden = [term for rule in case["expected"]["structural_assertions"]
                         if rule.get("type") == "forbidden_content" for term in rule.get("forbidden", [])]
            design_topics = observed_actual.get("teaching_design", {}).get("topics", [])
            review = assess_scope(adapted_assertion, design_topics, case["setup"], forbidden=forbidden)
            if review["status"] in {"NEEDS_SEMANTIC_REVIEW", "MISSING_OBSERVATION"}:
                unknown.append({"assertion_id": assertion["id"], "error": ("SCOPE_OBSERVATION_MISSING" if review["status"] == "MISSING_OBSERVATION" else "SEMANTIC_SCOPE_REVIEW_REQUIRED"), "scope_review": review})
                continue
            if review["status"] == "PASS_LEXICAL":
                observed_actual["teaching_design"]["topics"] = review["canonical"]
                adapted_fields.setdefault(assertion["id"], []).append("lexical aliases verified; no progression inference")
            # Explicit forbidden topics are left intact so the original structural grader fails.
        try:
            observation = structural_grader.grade(adapted_assertion, observed_actual, case["setup"])
            observations.append(observation)
        except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError) as exc:
            unknown.append({"assertion_id": assertion["id"], "error": f"{type(exc).__name__}: {exc}"})
        except Exception as exc:  # noqa: BLE001
            unknown.append({"assertion_id": assertion["id"], "error": f"JUDGE_ERROR {type(exc).__name__}: {exc}"})
    judge_setup_error = None
    try:
        artifacts = judge_prompt_artifacts(
            case, actual, judge_version=judge_version,
        )
        judge_input = artifacts.pop("judge_input")
        rendered_prompt = artifacts.pop("rendered_prompt")
    except Exception as exc:  # noqa: BLE001
        judge_setup_error = f"{type(exc).__name__}: {exc}"
        artifacts = {"judge_version": judge_version}
        judge_input = {}
        rendered_prompt = ""
    canonical_aliases = judge_input.get("canonical_question_type_aliases") or {}
    requested_counts = judge_input.get("canonical_requested_question_type_counts") or {}
    actual_counts = canonical_actual_counts(actual)
    attempts: list[dict[str, Any]] = []

    def judge_once(*, retry_reason: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        raw_response = None
        judge = None
        score = None
        error = None
        audit = None
        try:
            if judge_setup_error:
                raise ValueError(judge_setup_error)
            raw_response = dict(adapter.complete(rendered_prompt))
            judge = parse_case_judge_output(case, raw_response)
            audit = audit_judge_contract(
                raw_response,
                judge_version=judge_version,
                suite=case["suite"],
                canonical_aliases=canonical_aliases,
                canonical_requested_counts=requested_counts,
                actual_canonical_counts=actual_counts,
            )
            if not unknown:
                score = score_case(
                    observations, judge,
                    case["expected"]["behavioral_rubric"]["axes"],
                )
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        provenance = adapter.provenance() if hasattr(adapter, "provenance") else None
        provider_call = (provenance or {}).get("last_call")
        result = {
            "attempt": len(attempts) + 1,
            "retry_trigger": retry_reason,
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "provider_call": provider_call,
            "raw_response": raw_response,
            "behavioral": judge.model_dump(mode="json") if judge is not None else None,
            "score": score.model_dump(mode="json") if score is not None else None,
            "contract_audit": audit.model_dump(mode="json") if audit is not None else None,
            "error": error,
        }
        attempts.append(result)
        return result

    judge_started = time.perf_counter()
    first = judge_once()
    selected = first
    first_audit = first.get("contract_audit") or {}
    retry_trigger = None
    if first_audit.get("status") == "JUDGE_CONTRACT_VIOLATION":
        retry_trigger = "JUDGE_CONTRACT_VIOLATION"
        selected = judge_once(retry_reason=retry_trigger)

    selected_audit = selected.get("contract_audit") or {}
    if retry_trigger and (
        selected.get("score") is None
        or selected_audit.get("status") == "JUDGE_CONTRACT_VIOLATION"
    ):
        validity = "JUDGE_INVALID"
    elif selected.get("score") is not None:
        validity = "VALID"
    else:
        validity = "UNAVAILABLE"

    valid_score = selected.get("score") if validity == "VALID" else None
    judge_elapsed = round(time.perf_counter() - judge_started, 4)
    provider_config = (
        (adapter.provenance() or {}).get("config")
        if hasattr(adapter, "provenance") else None
    )
    generation_parameters = {
        key: provider_config.get(key)
        for key in (
            "temperature", "max_tokens", "timeout_seconds", "max_retries",
            "retry_base_seconds", "pacing_seconds",
        )
        if provider_config and key in provider_config
    }
    if provider_config:
        generation_parameters["response_format"] = {"type": "json_object"}
    audit_record = {
        **artifacts,
        "canonical_alias_contract": canonical_aliases,
        "canonical_requested_counts": requested_counts,
        "actual_canonical_counts": actual_counts,
        "provider_config": provider_config,
        "generation_parameters": generation_parameters or None,
        "attempts": attempts,
        "retry_trigger": retry_trigger,
        "final_validity": validity,
        "selected_attempt": selected["attempt"] if validity == "VALID" else None,
    }
    return {
        "status": (
            "SCORED" if validity == "VALID"
            else "JUDGE_INVALID" if validity == "JUDGE_INVALID"
            else "NOT_FULLY_SCORABLE"
        ),
        "judge_version": judge_version,
        "judge_elapsed_seconds": judge_elapsed,
        "judge_provider": adapter.provenance() if hasattr(adapter, "provenance") else None,
        "judge_audit": audit_record,
        "judge_validity": validity,
        "judge_contract_violation": any(
            (attempt.get("contract_audit") or {}).get("status")
            == "JUDGE_CONTRACT_VIOLATION"
            for attempt in attempts
        ),
        "structural": [item.model_dump(mode="json") for item in observations],
        "unscored_assertions": unknown,
        "observation_derivations": adapted_fields,
        "behavioral": selected.get("behavioral") if validity == "VALID" else None,
        "score": valid_score,
        "judge_error": selected.get("error"),
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temp.replace(path)


def constraint_assistance(message: str) -> str | None:
    """Render assisted clarification from business parsing, never a case ID/oracle."""
    from calculus_agent.runtime.request_guards import extract_generation_constraint_contract

    contract = extract_generation_constraint_contract(message)
    if not contract:
        return None
    canonical = contract["canonical"]
    requirements = canonical.get("question_type_requirements") or []
    if not requirements:
        return None
    parts = [f"{item['question_type']}{item['count']}道" for item in requirements]
    count = canonical.get("question_count")
    score = canonical.get("total_score")
    totals = "、".join(
        item for item in (
            f"共{count}题" if count else None,
            f"总分{score}分" if score else None,
        ) if item
    )
    return (
        "按系统 canonical 题型映射执行上一条教师请求："
        + "、".join(parts)
        + (f"；{totals}" if totals else "")
        + "。这些是已明确的硬约束，不需要再次向教师澄清。"
    )


def classify_execution(result: Any | None, actual: dict[str, Any]) -> str:
    if result is None:
        return "recovery_failure"
    errors = set(result.blocking_errors)
    if result.status == "needs_clarification":
        return "clarification"
    if "explicit_constraint_mismatch" in errors:
        return "constraint_violation"
    if result.status == "failed":
        return "provider_error" if any(code.startswith("provider_") for code in errors) else "recovery_failure"
    if result.status == "completed":
        return "success"
    return "recovery_failure"


def is_provider_exception(exc: BaseException) -> bool:
    return bool(
        getattr(exc, "provider_error_code", None)
        or any(term in f"{type(exc).__name__}: {exc}".lower() for term in (
            "ssl", "eof", "timed out", "connection", "urlerror",
        ))
    )


def run_case(case: dict[str, Any], *, source: Path | None, run_dir: Path,
             agent_model: str | None, grade: bool, judge_model: str | None,
             max_rounds: int, pacing: float, judge_version: str = "v1",
             execution_mode: str = "assisted") -> dict[str, Any]:
    db_path = run_dir / "databases" / f"{case['case_id']}.db"
    fixture_manifest = None
    fixture_builders = {}
    if case["case_id"] in {"TQ-PQ-001", "TQ-PQ-002", "TQ-PQ-003"}:
        if source is None:
            raise ValueError(f"{case['case_id']} requires the real source question database")
        from evaluation.teaching_quality.runners.pq001_fixture import build_pq001_fixture
        from evaluation.teaching_quality.runners.pq002_fixture import build_pq002_fixture
        from evaluation.teaching_quality.runners.pq003_fixture import build_pq003_fixture
        fixture_builders = {
            "TQ-PQ-001": build_pq001_fixture,
            "TQ-PQ-002": build_pq002_fixture,
            "TQ-PQ-003": build_pq003_fixture,
        }
        fixture_manifest = fixture_builders[case["case_id"]](source, db_path)
        atomic_json(run_dir / f"{case['case_id'].lower()}_fixture_manifest.json", fixture_manifest)
    else:
        make_case_db(source, db_path)
    url = f"sqlite:///{db_path.absolute()}"
    os.environ["CALCULUS_AGENT_DATABASE_URL"] = url
    from calculus_agent.config import Settings, get_settings
    from calculus_agent.db import build_session_factory, create_schema
    from calculus_agent.models import AgentPendingGeneration, Paper, TeacherAgentRunTrace, TeacherAgentSpan
    from sqlalchemy import select
    from calculus_agent.runtime.coordinator import build_teacher_agent_backend, run_teacher_agent
    from evaluation.reliability.execution_control import require_live_llm_authorization

    require_live_llm_authorization()
    get_settings.cache_clear()
    settings = Settings(database_url=url)
    if agent_model:
        settings = settings.model_copy(update={"siliconflow_agent_model": agent_model})
    if not settings.siliconflow_api_key:
        raise RuntimeError("SILICONFLOW_API_KEY not configured in environment/.env")
    backend = build_teacher_agent_backend(settings)
    if backend is None:
        raise RuntimeError("Teacher Agent backend unavailable")
    create_schema(url)
    factory = build_session_factory(url)
    turns = []
    workflow_checks: dict[str, Any] = {}
    state_transitions: list[dict[str, Any]] = []
    started = time.perf_counter()
    conversation_id = f"tq-dev-{case['case_id']}-{uuid4().hex[:10]}"
    owner_key = f"tq-eval-{uuid4().hex}"
    last_result = None
    last_actual: dict[str, Any] = {}
    with factory() as session:
        def execute_turn(message: str, *, kind: str) -> tuple[Any, dict[str, Any]]:
            with session.begin():
                result = run_teacher_agent(
                    session, message, backend=backend,
                    conversation_id=conversation_id, owner_key=owner_key,
                    max_tool_rounds=max_rounds,
                )
                actual = normalized_output(result, session)
                trace = session.scalar(
                    select(TeacherAgentRunTrace)
                    .where(TeacherAgentRunTrace.run_id == result.run_id)
                ) if result.run_id else None
                spans = list(session.scalars(
                    select(TeacherAgentSpan)
                    .where(TeacherAgentSpan.run_id == result.run_id)
                    .order_by(TeacherAgentSpan.started_at, TeacherAgentSpan.id)
                )) if result.run_id else []
                turns.append({
                    "turn_kind": kind,
                    "user_message": message,
                    "result": result.model_dump(mode="json"),
                    "actual_output": actual,
                    "trace": {
                        "status": trace.status,
                        "result_status": trace.result_status,
                        "latency_ms": trace.latency_ms,
                        "tool_calls": trace.tool_calls_json,
                        "state_before": trace.state_before_json,
                        "state_after": trace.state_after_json,
                        "error_code": trace.error_code,
                        "error_message": trace.error_message,
                        "spans": [{
                            "type": span.span_type, "name": span.name,
                            "status": span.status, "latency_ms": span.latency_ms,
                            "output": span.output_json,
                        } for span in spans],
                    } if trace else None,
                })
                return result, actual

        for turn in case["user_turns"]:
            if turn["role"] != "user":
                raise ValueError("only user turns are supported; conversation replay contract required")
            last_result, last_actual = execute_turn(turn["content"], kind="original_request")
            state_transitions.append({"event": "agent_prepare_result", "status": last_result.status})
            if pacing:
                time.sleep(pacing)

        assistance_messages: list[str] = []
        assistance = (
            constraint_assistance(case["user_turns"][-1]["content"])
            if execution_mode == "assisted"
            and last_result and last_result.generation_preview is None
            else None
        )
        if assistance:
            assistance_messages.append(assistance)
            last_result, last_actual = execute_turn(
                assistance, kind="business_constraint_clarification",
            )
            state_transitions.append({
                "event": "constraint_contract_replayed",
                "status": last_result.status,
            })
            if pacing:
                time.sleep(pacing)

        if case["case_id"] in {"TQ-PQ-001", "TQ-PQ-002", "TQ-PQ-003"}:
            with session.begin():
                pending_row = session.get(AgentPendingGeneration, conversation_id)
                pending_payload = pending_row.payload_json if pending_row else None
            preview = last_result.generation_preview if last_result else None
            request = (pending_payload or {}).get("request", {})
            prepare_called = any(
                call.get("tool_name") == "prepare_generation_plan"
                for call in ((turns[-1].get("trace") or {}).get("tool_calls") or [])
            )
            scope_names = request.get("scope_names") or []
            type_counts = {
                item["question_type"]: item["count"]
                for item in request.get("question_type_requirements") or []
            }
            if case["case_id"] == "TQ-PQ-001":
                expected_plan = (
                    request.get("question_count") == 6
                    and request.get("total_score") == 60
                    and any(name.startswith("第一章") for name in scope_names)
                )
            elif case["case_id"] == "TQ-PQ-002":
                expected_plan = (
                    request.get("question_count") == 14
                    and request.get("total_score") == 100
                    and any(name.startswith("第三章") for name in scope_names)
                    and type_counts == {"选择题": 4, "填空题": 4, "计算题": 4, "证明题": 2}
                )
            else:
                expected_plan = (
                    request.get("question_count") == 10
                    and request.get("total_score") == 100
                    and all(any(name.startswith(chapter) for name in scope_names) for chapter in ("第一章", "第二章", "第三章"))
                    and not any(name.startswith("第四章") for name in scope_names)
                )
            plan_legal = bool(
                prepare_called
                and last_result
                and last_result.status == "waiting_confirmation"
                and preview and preview.ok
                and not preview.blocking_errors and not preview.clarification_questions
                and pending_payload
                and expected_plan
            )
            workflow_checks.update({
                "prepare_generation_plan_called": prepare_called,
                "prepare_generation_plan_succeeded": bool(preview and preview.ok),
                "pending_generation_exists": pending_payload is not None,
                "pending_status_correct": bool(last_result and last_result.status == "waiting_confirmation"),
                "pending_payload": pending_payload,
                "plan_legal_for_confirmation": plan_legal,
                "auto_confirmation_blocked": not plan_legal,
            })
            if plan_legal:
                state_transitions.append({"event": "pending_persisted", "status": "waiting_confirmation"})
                confirmation = "确认，按这个方案生成试卷。"
                last_result, last_actual = execute_turn(confirmation, kind="teacher_confirmation")
                if pacing:
                    time.sleep(pacing)
                paper_id = str(last_result.paper.paper_id) if last_result.paper and last_result.paper.paper_id else None
                with session.begin():
                    persisted_paper = session.get(Paper, paper_id) if paper_id else None
                    pending_after = session.get(AgentPendingGeneration, conversation_id)
                confirm_called = any(
                    call.get("tool_name") == "confirm_generation"
                    for call in ((turns[-1].get("trace") or {}).get("tool_calls") or [])
                )
                workflow_checks.update({
                    "teacher_confirmation_sent": True,
                    "confirm_generation_called": confirm_called,
                    "confirm_generation_succeeded": bool(last_result.paper and last_result.paper.ok),
                    "paper_id": paper_id,
                    "paper_persisted": persisted_paper is not None,
                    "pending_cleared_after_confirmation": pending_after is None,
                })
                state_transitions.append({
                    "event": "teacher_confirmation_result",
                    "status": last_result.status,
                    "paper_id": paper_id,
                })
            else:
                workflow_checks.update({
                    "teacher_confirmation_sent": False,
                    "confirm_generation_called": False,
                    "confirm_generation_succeeded": False,
                    "paper_persisted": False,
                })
    output: dict[str, Any] = {
        "case_id": case["case_id"],
        "suite": case["suite"],
        "agent_status": last_result.status if last_result else "NOT_RUN",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "turns": turns,
        "state_transitions": state_transitions,
        "workflow_checks": workflow_checks,
        "execution_mode": execution_mode,
        "assistance_messages": assistance_messages,
        "clarification_required": bool(
            assistance_messages
            or any(turn["result"].get("status") == "needs_clarification" for turn in turns)
        ),
        "execution_outcome": classify_execution(last_result, last_actual),
        "request_completed": bool(last_result and last_result.status == "completed"),
        "actual_output": last_actual,
        "environment": {
            "source_database": str(source) if source else None,
            "case_database": str(db_path),
            "agent_model": settings.siliconflow_agent_model,
            "fixture_applied": fixture_manifest is not None,
            "fixture_manifest": fixture_manifest,
            "setup_injected": fixture_manifest is not None,
            "evaluation_note": (
                f"{case['case_id']} real-question fixture loaded and validated."
                if fixture_manifest else
                "Case setup is NOT seeded into SQLite. Scores are diagnostic only."
            ),
        },
    }
    has_real_artifact = (case["suite"] == "consultation" or
                         case["suite"] == "paper_quality" and "paper" in last_actual or
                         case["suite"] == "teaching_design" and "teaching_design" in last_actual)
    can_grade = (last_result is not None and
                 (last_result.status == "completed" or
                  last_result.status == "waiting_confirmation" and
                  "teaching_design" in last_actual and
                  case["suite"] in {"consultation", "teaching_design"}))
    if grade and can_grade and has_real_artifact:
        from evaluation.teaching_quality.judge.provider import build_siliconflow_judge_adapter
        judge_settings = settings
        if judge_model:
            judge_settings = settings.model_copy(update={"siliconflow_agent_model": judge_model})
        adapter = build_siliconflow_judge_adapter(judge_settings, max_tokens=2048, pacing_seconds=pacing)
        output["grading"] = grade_result(
            case, last_actual, adapter, judge_version=judge_version,
        )
    else:
        reason = ("NOT_REQUESTED" if not grade else
                  "NOT_SCORABLE_AGENT_STATUS" if not can_grade else
                  "MISSING_STRUCTURED_ARTIFACT")
        output["grading"] = {"status": reason, "judge_version": judge_version}
    output["end_to_end_elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Frozen Dev-only Tutor capture and optional quality grading")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--case-id", type=str)
    parser.add_argument("--execute", action="store_true", help="Explicitly allow live SiliconFlow model calls")
    parser.add_argument("--grade", action="store_true", help="Run live Judge calls after Tutor")
    parser.add_argument("--allow-unseeded-paper", action="store_true", help="Run paper cases despite missing controlled fixture loading; diagnostic only")
    parser.add_argument("--agent-model", type=str)
    parser.add_argument("--judge-model", type=str)
    parser.add_argument("--judge-version", choices=("v1", "v2"), default="v1")
    parser.add_argument("--execution-mode", choices=("unassisted", "assisted"), default="assisted")
    parser.add_argument("--max-tool-rounds", type=int, default=8)
    parser.add_argument("--pacing-seconds", type=float, default=2)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    cases, manifest = load_dev_cases()
    if args.list:
        for case in cases:
            print(f"{case['case_id']}\t{case['suite']}\t{case['title']}")
        print(f"DEV={len(cases)} BLIND_EXECUTED=False")
        return
    if args.smoke:
        cases = [next(case for case in cases if case["case_id"] == "TQ-CQ-006")]
    elif args.case_id:
        cases = [next((case for case in cases if case["case_id"] == args.case_id), None)]
        if cases[0] is None:
            parser.error("--case-id must belong to frozen Dev; Blind IDs are blocked")
    if not args.execute:
        parser.error("--execute is required for any live model invocation")
    from calculus_agent.config import Settings

    source = source_database(Settings())
    if args.all and source is None:
        parser.error("24-case run requires an existing SQLite database; no empty DB benchmark")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.output_dir or REPORTS / "dev_eval_runs" / f"dev-{timestamp}-{uuid4().hex[:6]}"
    if run_dir.exists():
        parser.error("output directory already exists: will not overwrite")
    run_dir.mkdir(parents=True)
    report: dict[str, Any] = {
        "kind": "diagnostic_real_agent_dev_capture",
        "status": "in_progress",
        "created_at": datetime.now(UTC).isoformat(),
        "manifest_sha256": sha(ROOT / "datasets/manifests/split_v1.yaml"),
        "dev_case_count": len(cases),
        "blind_executed": False,
        "setup_fixture_loaded": any(case["case_id"] in {"TQ-PQ-001", "TQ-PQ-002", "TQ-PQ-003"} for case in cases),
        "official_benchmark": False,
        "source_database_found": source is not None,
        "results": [],
    }
    report_path = run_dir / "report.json"
    atomic_json(report_path, report)
    try:
        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['case_id']} {case['suite']}", flush=True)
            if case["suite"] == "paper_quality" and case["case_id"] not in {"TQ-PQ-001", "TQ-PQ-002", "TQ-PQ-003"} and not args.allow_unseeded_paper:
                item = {"case_id": case["case_id"], "suite": case["suite"],
                        "execution_status": "SKIPPED_UNSEEDED_FIXTURE",
                        "grading": {"status": "NOT_SCORABLE"},
                        "reason": "Paper fixture is not loaded into the isolated case database"}
                report["results"].append(item)
                atomic_json(report_path, report)
                print("  SKIPPED_UNSEEDED_FIXTURE", flush=True)
                continue
            try:
                item = run_case(case, source=source, run_dir=run_dir,
                                agent_model=args.agent_model, grade=args.grade,
                                judge_model=args.judge_model, max_rounds=args.max_tool_rounds,
                                pacing=args.pacing_seconds, judge_version=args.judge_version,
                                execution_mode=args.execution_mode)
                item["execution_status"] = "CAPTURED"
            except Exception as exc:  # noqa: BLE001
                outcome = "provider_error" if is_provider_exception(exc) else "recovery_failure"
                item = {"case_id": case["case_id"], "suite": case["suite"],
                        "execution_status": "PROVIDER_ERROR" if outcome == "provider_error" else "ERROR",
                        "execution_outcome": outcome,
                        "error": f"{type(exc).__name__}: {exc}"}
            report["results"].append(item)
            atomic_json(report_path, report)
            print(f"  {item['execution_status']} / {item.get('agent_status', 'N/A')}", flush=True)
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        atomic_json(report_path, report)
        print(f"Interrupted. Partial results: {report_path}")
        raise SystemExit(130) from None
    report["status"] = "complete"
    report["summary"] = {
        "execution": dict(Counter(row["execution_status"] for row in report["results"])),
        "agent": dict(Counter(row.get("agent_status", "UNKNOWN") for row in report["results"])),
        "grading": dict(Counter(row.get("grading", {}).get("status", "UNAVAILABLE") for row in report["results"])),
        "outcomes": dict(Counter(row.get("execution_outcome", "unknown") for row in report["results"])),
    }
    atomic_json(report_path, report)
    print(f"REPORT={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print("NOTE: Blind was NOT run; PQ-001/PQ-002/PQ-003 use validated isolated fixtures.")


if __name__ == "__main__":
    main()

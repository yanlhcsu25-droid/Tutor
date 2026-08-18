from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

# 必须尽早加载，避免某些配置模块在 import 时读取不到 .env
load_dotenv()

from sqlalchemy import select

from calculus_agent.agent.agent import (
    build_teacher_agent_backend,
    run_teacher_agent,
)
from calculus_agent.agent.conversation_state import (
    DatabasePendingReplacementStore,
    PendingGeneration,
)
from calculus_agent.agent.schemas import (
    AgentWorkingMemory,
    GeneratePaperInput,
)
from calculus_agent.config import get_settings

from tests.conftest import create_isolated_test_session
from tests.evals.case_loader import (
    EvalCase,
    filter_cases,
    load_eval_suite,
)
from tests.evals.curriculum_fixture import seed_eval_curriculum
from tests.evals.fixtures.context import EvalFixtureContext
from tests.evals.fixtures.paper import seed_current_paper
from tests.evals.graders.constraint_grader import grade_constraints
from tests.evals.graders.score_grader import grade_score
from tests.evals.graders.state_grader import grade_state


# ============================================================
# Paths
# ============================================================

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CASE_FILE = (
    ROOT
    / "tests"
    / "evals"
    / "cases"
    / "teacher_agent_regression.yaml"
)

DEFAULT_REPORT = (
    ROOT
    / "tests"
    / "evals"
    / "reports"
    / "latest.json"
)


# ============================================================
# Serialization
# ============================================================


def to_jsonable(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(key): to_jsonable(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(item) for item in value]

    if hasattr(value, "model_dump"):
        return to_jsonable(
            value.model_dump(mode="json")
        )

    if is_dataclass(value):
        return to_jsonable(asdict(value))

    return str(value)


def decode_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    stripped = value.strip()

    if not stripped:
        return value

    if not (
        stripped.startswith("{")
        or stripped.startswith("[")
    ):
        return value

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


# ============================================================
# Eval DB
# ============================================================


def create_eval_session():
    """
    每个 Eval Case 使用独立 SQLite Session，
    不连接生产数据库。
    """
    return create_isolated_test_session()


# ============================================================
# Real LLM Backend
# ============================================================


def create_eval_backend(case: EvalCase):
    """
    正常 case 使用真实 Teacher Agent backend。

    error case 可通过 backend.behavior=raise 使用本地 backend，
    避免真实调用模型。
    """

    backend_config = case.backend or {}

    if backend_config.get("behavior") == "raise":
        exception_config = (
            backend_config.get("exception") or {}
        )

        message = exception_config.get(
            "message",
            "simulated llm error",
        )

        class RaisingBackend:
            model = "eval-raising-backend"

            def complete(
                self,
                messages,
                tools,
                **kwargs,
            ):
                raise RuntimeError(message)

        return RaisingBackend()

    settings = get_settings()

    backend = build_teacher_agent_backend(settings)

    if backend is None:
        raise RuntimeError(
            "Teacher Agent real backend unavailable. "
            "请确认已配置模型 API Key / Base URL / Model。"
        )

    return backend


# ============================================================
# SQLAlchemy model helpers
# ============================================================


MODEL_MODULES = (
    "calculus_agent.models",
    "calculus_agent.agent.models",
    "calculus_agent.agent.state",
)

PAYLOAD_FIELDS = (
    "payload_json",
    "memory_json",
    "state_json",
    "data_json",
    "payload",
)


def find_model_class(
    *class_names: str,
):
    for module_name in MODEL_MODULES:
        try:
            module = importlib.import_module(
                module_name
            )
        except ImportError:
            continue

        for class_name in class_names:
            cls = getattr(
                module,
                class_name,
                None,
            )

            if cls is not None:
                return cls

    return None


def latest_row_for_conversation(
    session,
    model_class,
    conversation_id: str,
):
    if model_class is None:
        return None

    statement = select(model_class)

    if hasattr(model_class, "conversation_id"):
        statement = statement.where(
            model_class.conversation_id
            == conversation_id
        )

    if hasattr(model_class, "updated_at"):
        statement = statement.order_by(
            model_class.updated_at.desc()
        )

    elif hasattr(model_class, "created_at"):
        statement = statement.order_by(
            model_class.created_at.desc()
        )

    elif hasattr(model_class, "id"):
        statement = statement.order_by(
            model_class.id.desc()
        )

    return session.scalar(
        statement.limit(1)
    )


def row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}

    table = getattr(
        row,
        "__table__",
        None,
    )

    if table is None:
        return {}

    result = {}

    for column in table.columns:
        value = getattr(
            row,
            column.name,
            None,
        )

        result[column.name] = to_jsonable(
            decode_json_value(value)
        )

    return result


def extract_record_payload(
    record: dict[str, Any],
) -> dict[str, Any]:
    for key in PAYLOAD_FIELDS:
        value = record.get(key)
        value = decode_json_value(value)

        if isinstance(value, dict):
            return value

    return record


def _payload_field_for_model(model_class) -> str:
    table = getattr(
        model_class,
        "__table__",
        None,
    )

    if table is None:
        raise RuntimeError(
            f"{model_class} 不是可识别的 SQLAlchemy ORM Model"
        )

    column_names = {
        column.name
        for column in table.columns
    }

    for field in PAYLOAD_FIELDS:
        if field in column_names:
            return field

    raise RuntimeError(
        f"{model_class.__name__} 找不到 payload JSON 字段。"
        f"现有字段：{sorted(column_names)}"
    )


def _encode_payload_for_column(
    model_class,
    field_name: str,
    payload: dict[str, Any],
) -> Any:
    """
    JSON/JSONB 列直接写 dict；
    Text/String 列写 JSON string。
    """
    table = model_class.__table__
    column = table.columns[field_name]

    try:
        python_type = column.type.python_type
    except (AttributeError, NotImplementedError):
        python_type = None

    if python_type in (dict, list):
        return payload

    return json.dumps(
        payload,
        ensure_ascii=False,
    )


def _ensure_record_identity(
    row: Any,
) -> None:
    """
    只补显式 string UUID 主键。
    int autoincrement / server default 不干预。
    """
    table = getattr(
        row,
        "__table__",
        None,
    )

    if table is None:
        return

    for column in table.primary_key.columns:
        if getattr(row, column.name, None) is not None:
            continue

        if (
            column.default is not None
            or column.server_default is not None
            or column.autoincrement is True
        ):
            continue

        try:
            python_type = column.type.python_type
        except (AttributeError, NotImplementedError):
            python_type = None

        if python_type is str:
            setattr(
                row,
                column.name,
                str(uuid4()),
            )


def upsert_payload_record(
    *,
    session,
    model_class,
    conversation_id: str,
    payload: dict[str, Any],
) -> None:
    """
    Test fixture 专用：
    直接、确定性写入 ORM 状态，不经过 LLM。
    """

    if model_class is None:
        raise RuntimeError(
            "无法写入 eval fixture：对应 ORM Model 不存在。"
        )

    row = latest_row_for_conversation(
        session,
        model_class,
        conversation_id,
    )

    if row is None:
        row = model_class()

    if hasattr(row, "conversation_id"):
        row.conversation_id = conversation_id

    payload_field = _payload_field_for_model(
        model_class
    )

    setattr(
        row,
        payload_field,
        _encode_payload_for_column(
            model_class,
            payload_field,
            payload,
        ),
    )

    now = datetime.now(timezone.utc)

    if (
        hasattr(row, "created_at")
        and getattr(row, "created_at", None) is None
    ):
        row.created_at = now

    if hasattr(row, "updated_at"):
        row.updated_at = now

    _ensure_record_identity(row)

    session.add(row)
    session.flush()


# ============================================================
# Working Memory
# ============================================================


def load_working_memory(
    session,
    conversation_id: str,
) -> dict[str, Any]:

    memory_cls = find_model_class(
        "AgentWorkingMemoryRecord",
        "AgentWorkingMemory",
    )

    row = latest_row_for_conversation(
        session,
        memory_cls,
        conversation_id,
    )

    record = row_to_dict(row)

    return extract_record_payload(record)


def normalize_generation_plan(
    plan: dict[str, Any],
) -> dict[str, Any]:
    """
    把 YAML generation_plan 规范成 Working Memory 中
    generation_summary 的完整形状。

    重点：
    - 不让 LLM 解释 fixture；
    - 保留 YAML 明确给出的值；
    - 可确定计算的 total_score / question_count 直接由 Python 计算。
    """

    normalized = {
        "paper_type": plan.get("paper_type"),
        "scope_names": plan.get("scope_names"),
        "audience": plan.get("audience"),
        "question_count": plan.get("question_count"),
        "total_score": plan.get("total_score"),
        "question_type_requirements": plan.get(
            "question_type_requirements"
        ),
        "knowledge_preferences": plan.get(
            "knowledge_preferences"
        ),
        "difficulty_level": plan.get(
            "difficulty_level"
        ),
        "difficulty_ratio": plan.get(
            "difficulty_ratio"
        ),
        "difficulty_preference": plan.get(
            "difficulty_preference"
        ),
        "diversity_preference": plan.get(
            "diversity_preference"
        ),
    }

    requirements = normalized.get(
        "question_type_requirements"
    )

    if isinstance(requirements, list):
        normalized_requirements = []

        for item in requirements:
            if not isinstance(item, dict):
                continue

            current = dict(item)

            count = current.get("count")
            score_each = current.get("score_each")

            if (
                current.get("total_score") is None
                and isinstance(count, int)
                and isinstance(score_each, (int, float))
            ):
                current["total_score"] = (
                    count * score_each
                )

            normalized_requirements.append(
                current
            )

        normalized[
            "question_type_requirements"
        ] = normalized_requirements

        if normalized.get("question_count") is None:
            counts = [
                item.get("count")
                for item in normalized_requirements
                if isinstance(
                    item.get("count"),
                    int,
                )
            ]

            if (
                counts
                and len(counts)
                == len(normalized_requirements)
            ):
                normalized["question_count"] = sum(
                    counts
                )

        if normalized.get("total_score") is None:
            scores = [
                item.get("total_score")
                for item in normalized_requirements
                if isinstance(
                    item.get("total_score"),
                    (int, float),
                )
            ]

            if (
                scores
                and len(scores)
                == len(normalized_requirements)
            ):
                normalized["total_score"] = sum(
                    scores
                )

    return normalized


def _generation_store(session):
    return DatabasePendingReplacementStore(session)


def _project_pending_generation(
    *,
    store: DatabasePendingReplacementStore,
    conversation_id: str,
    pending: PendingGeneration,
) -> None:
    memory = AgentWorkingMemory(
        active_task={
            "type": "generation",
            "status": "awaiting_confirmation",
        },
        generation_summary=pending.request.model_dump(mode="json"),
        last_clarification=None,
        last_completed_paper=None,
        unsupported_preferences=[],
    )
    store.set_memory(conversation_id, memory)


def _persist_pending_generation(
    *,
    session,
    conversation_id: str,
    pending: PendingGeneration,
) -> PendingGeneration:
    store = _generation_store(session)
    stored = store.set_generation(
        conversation_id,
        pending,
    )
    _project_pending_generation(
        store=store,
        conversation_id=conversation_id,
        pending=stored,
    )
    return stored


def _pending_generation_from_fixture(
    payload: dict[str, Any],
) -> PendingGeneration:
    fixture = dict(payload)
    fixture.pop("complete", None)

    if "request" in fixture:
        return PendingGeneration.model_validate(fixture)

    request_payload = {
        key: value
        for key, value in fixture.items()
        if key in GeneratePaperInput.model_fields
    }
    request = GeneratePaperInput.model_validate(
        normalize_generation_plan(request_payload)
    )

    return PendingGeneration(
        request=request,
        total_score_source=fixture.get(
            "total_score_source",
            (
                "teacher_explicit"
                if request.total_score is not None
                else "default_template"
            ),
        ),
        locked_score_question_types=list(
            fixture.get("locked_score_question_types") or []
        ),
    )


def seed_generation_plan(
    *,
    session,
    conversation_id: str,
    plan: dict[str, Any],
) -> None:
    generation = normalize_generation_plan(plan)

    _persist_pending_generation(
        session=session,
        conversation_id=conversation_id,
        pending=PendingGeneration(
            request=GeneratePaperInput.model_validate(generation),
            total_score_source=(
                "teacher_explicit"
                if plan.get("total_score") is not None
                else "default_template"
            ),
            locked_score_question_types=[],
        ),
    )


# ============================================================
# Pending State
# ============================================================


def load_pending_generation(
    session,
    conversation_id: str,
) -> dict[str, Any] | None:
    pending = _generation_store(session).get_generation(
        conversation_id
    )
    if pending is None:
        return None

    return {
        **pending.request.model_dump(mode="json"),
        "total_score_source": pending.total_score_source,
        "locked_score_question_types": list(
            pending.locked_score_question_types
        ),
        "pending_version": pending.pending_version,
    }


def seed_pending_generation(
    *,
    session,
    conversation_id: str,
    payload: dict[str, Any],
) -> None:
    _persist_pending_generation(
        session=session,
        conversation_id=conversation_id,
        pending=_pending_generation_from_fixture(payload),
    )


def load_pending_replacement(
    session,
    conversation_id: str,
) -> dict[str, Any] | None:
    pending = DatabasePendingReplacementStore(session).get(
        conversation_id
    )
    return pending.model_dump(mode="json") if pending else None


def seed_pending_replacement(
    *,
    session,
    conversation_id: str,
    payload: dict[str, Any],
) -> None:
    cls = find_model_class(
        "PendingReplacementRecord",
        "AgentPendingReplacementRecord",
        "PendingReplacement",
    )

    if cls is None:
        raise RuntimeError(
            "setup.pending_replacement 需要真实 replacement pending ORM，"
            "但 Runner 未找到对应 Model。"
        )

    upsert_payload_record(
        session=session,
        model_class=cls,
        conversation_id=conversation_id,
        payload=payload,
    )


# ============================================================
# Setup / Fixtures
# ============================================================


SUPPORTED_SETUP_KEYS = {
    "generation_plan",
    "pending_generation",
    "pending_replacement",
    "current_paper",
}


def apply_case_setup(
    *,
    session,
    conversation_id: str,
    case: EvalCase,
) -> EvalFixtureContext:
    setup = case.setup or {}
    fixture_context = EvalFixtureContext()

    if not setup:
        return fixture_context

    unsupported = (
        set(setup.keys())
        - SUPPORTED_SETUP_KEYS
    )

    if unsupported:
        raise RuntimeError(
            f"{case.id} 当前 Runner 尚未实现这些 setup fixture："
            f"{sorted(unsupported)}。"
            "不要把它们转换成 synthetic user turn；"
            "应为对应业务状态补确定性 fixture builder。"
        )

    current_paper = setup.get(
        "current_paper"
    )

    if current_paper is not None:
        if not isinstance(
            current_paper,
            dict,
        ):
            raise ValueError(
                f"{case.id}.setup.current_paper 必须是 object"
            )

        fixture_context = seed_current_paper(
            session,
            current_paper,
        )

    generation_plan = setup.get(
        "generation_plan"
    )

    if generation_plan is not None:
        if not isinstance(
            generation_plan,
            dict,
        ):
            raise ValueError(
                f"{case.id}.setup.generation_plan 必须是 object"
            )

        seed_generation_plan(
            session=session,
            conversation_id=conversation_id,
            plan=generation_plan,
        )

    pending_generation = setup.get(
        "pending_generation"
    )

    if pending_generation is not None:
        if not isinstance(
            pending_generation,
            dict,
        ):
            raise ValueError(
                f"{case.id}.setup.pending_generation 必须是 object"
            )

        seed_pending_generation(
            session=session,
            conversation_id=conversation_id,
            payload=pending_generation,
        )

    pending_replacement = setup.get(
        "pending_replacement"
    )

    if pending_replacement is not None:
        if not isinstance(
            pending_replacement,
            dict,
        ):
            raise ValueError(
                f"{case.id}.setup.pending_replacement 必须是 object"
            )

        seed_pending_replacement(
            session=session,
            conversation_id=conversation_id,
            payload=pending_replacement,
        )

    session.commit()
    session.expire_all()

    return fixture_context


# ============================================================
# Trace
# ============================================================


def load_latest_trace(
    session,
    conversation_id: str,
) -> dict[str, Any]:

    trace_cls = find_model_class(
        "TeacherAgentRunTrace",
    )

    row = latest_row_for_conversation(
        session,
        trace_cls,
        conversation_id,
    )

    return row_to_dict(row)


def extract_tool_calls(
    trace: dict[str, Any],
) -> list[dict[str, Any]]:

    value = (
        trace.get("tool_calls_json")
        or trace.get("tool_calls")
        or []
    )

    value = decode_json_value(value)

    if isinstance(value, list):
        return value

    return []


# ============================================================
# Actual State Adapter
# ============================================================


def collect_actual_state(
    *,
    session,
    conversation_id: str,
    result: Any | None,
) -> dict[str, Any]:

    result_dict = (
        to_jsonable(result)
        if result is not None
        else {}
    ) or {}

    memory = load_working_memory(
        session,
        conversation_id,
    )

    active_task = (
        memory.get("active_task")
        if isinstance(memory, dict)
        else {}
    ) or {}

    generation = {}

    if isinstance(memory, dict):
        generation = (
            memory.get("generation_summary")
            or memory.get("generation")
            or {}
        )

    clarification = {}

    if isinstance(memory, dict):
        clarification = (
            memory.get("last_clarification")
            or memory.get("clarification")
            or {}
        )

    last_completed_paper = {}

    if isinstance(memory, dict):
        last_completed_paper = (
            memory.get("last_completed_paper")
            or {}
        )

    trace = load_latest_trace(
        session,
        conversation_id,
    )

    tool_calls = extract_tool_calls(
        trace
    )

    pending_generation = (
        load_pending_generation(
            session,
            conversation_id,
        )
    )

    pending_replacement = (
        load_pending_replacement(
            session,
            conversation_id,
        )
    )

    status = (
        result_dict.get("status")
        or result_dict.get("result_status")
        or trace.get("result_status")
    )

    message = (
        result_dict.get("message")
        or result_dict.get("final_response")
        or trace.get("final_response")
        or ""
    )

    error = None

    if trace.get("error_code"):
        error = {
            "code": trace.get("error_code"),
            "type": trace.get("error_type"),
            "stage": trace.get("error_stage"),
            "message": trace.get(
                "error_message"
            ),
        }

    paper = dict(
        last_completed_paper
        if isinstance(
            last_completed_paper,
            dict,
        )
        else {}
    )

    for key in (
        "paper_id",
        "version_id",
    ):
        value = (
            result_dict.get(key)
            or trace.get(key)
        )

        if value is not None:
            paper[key] = value

    return {
        "status": status,
        "message": message,

        "result": result_dict,

        "working_memory": memory,
        "active_task": active_task,
        "generation": generation,
        "clarification": clarification,

        "pending_generation": pending_generation,
        "pending_replacement": pending_replacement,

        "paper": paper,

        "trace": {
            **trace,
            "tool_calls": tool_calls,
        },

        "error": error,
    }


# ============================================================
# Turns
# ============================================================


def build_case_turns(
    case: EvalCase,
) -> list[dict[str, Any]]:
    """
    这里只返回真正的用户 turns。

    setup 永远不进入这里。
    """
    return list(case.turns or [])


# ============================================================
# Grader Dispatcher
# ============================================================


def run_graders(
    case: EvalCase,
    actual: dict[str, Any],
) -> list[dict[str, Any]]:

    results: list[dict[str, Any]] = []

    for grader_config in case.graders:

        grader_type = grader_config.get(
            "type"
        )

        if grader_type == "state":
            results.append(
                grade_state(
                    case,
                    actual,
                )
            )

        elif grader_type == "score":
            results.append(
                grade_score(
                    case,
                    actual,
                )
            )

        elif grader_type == (
            "constraint_preservation"
        ):
            results.append(
                grade_constraints(
                    case,
                    actual,
                )
            )

        elif grader_type in {
            "semantic",
            "paper_state",
            "schema",
            "error",
            "trace",
            "tool_rule",
            "context",
        }:
            results.append(
                {
                    "grader": grader_type,
                    "passed": True,
                    "skipped": True,
                    "errors": [],
                    "reason": (
                        "grader_not_implemented_in_v0"
                    ),
                }
            )

        else:
            results.append(
                {
                    "grader": grader_type,
                    "passed": False,
                    "skipped": False,
                    "errors": [
                        f"unknown grader type: "
                        f"{grader_type}"
                    ],
                }
            )

    return results


# ============================================================
# Run One Case
# ============================================================


def run_case(
    case: EvalCase,
) -> dict[str, Any]:

    conversation_id = (
        f"eval-{case.id}-"
        f"{uuid4().hex[:8]}"
    )

    session = create_eval_session()

    # Deterministic, isolated curriculum so generation scopes (e.g. 第三章)
    # resolve exactly like production without touching the developer's DB.
    seed_eval_curriculum(session)

    turn_results: list[dict] = []

    try:
        # ----------------------------------------------------
        # 1. fixture 先确定性落地
        # ----------------------------------------------------

        fixture_context = apply_case_setup(
            session=session,
            conversation_id=conversation_id,
            case=case,
        )

        # ----------------------------------------------------
        # 2. setup 完成后才创建 backend
        # ----------------------------------------------------

        backend = create_eval_backend(
            case
        )

        turns = build_case_turns(
            case
        )

        if not turns:
            raise RuntimeError(
                f"{case.id}: no real user turns to execute"
            )

        # ----------------------------------------------------
        # 3. initial_state = setup 后、第一轮用户输入前
        # ----------------------------------------------------

        initial_state = collect_actual_state(
            session=session,
            conversation_id=conversation_id,
            result=None,
        )

        result = None

        # constraint grader 比较：
        # 最后一轮真实用户输入前 vs 最后一轮后
        before_last_turn = initial_state

        for index, turn in enumerate(
            turns,
            start=1,
        ):
            user_message = turn.get(
                "user"
            )

            if not user_message:
                raise ValueError(
                    f"{case.id} turn {index}: "
                    f"missing user message"
                )

            pre_turn_state = (
                collect_actual_state(
                    session=session,
                    conversation_id=(
                        conversation_id
                    ),
                    result=result,
                )
            )

            if index == len(turns):
                before_last_turn = (
                    pre_turn_state
                )

            # ------------------------------------------------
            # 只有这里才进入真实 Agent / LLM
            # ------------------------------------------------

            result = run_teacher_agent(
                session,
                user_message,
                conversation_id=(
                    conversation_id
                ),
                paper_id=fixture_context.paper_id,
                version_id=fixture_context.version_id,
                backend=backend,
            )

            session.commit()
            session.expire_all()

            post_turn_state = (
                collect_actual_state(
                    session=session,
                    conversation_id=(
                        conversation_id
                    ),
                    result=result,
                )
            )

            turn_results.append(
                {
                    "turn": index,
                    "synthetic_setup": False,
                    "user": user_message,
                    "result": to_jsonable(
                        result
                    ),
                    "state": post_turn_state,
                }
            )

        actual = collect_actual_state(
            session=session,
            conversation_id=conversation_id,
            result=result,
        )

        actual["before"] = (
            before_last_turn
        )

        actual["fixture_state"] = (
            initial_state
        )

        grader_results = run_graders(
            case,
            actual,
        )

        executed_graders = [
            grader
            for grader in grader_results
            if not grader.get("skipped")
        ]

        passed = (
            bool(executed_graders)
            and all(
                grader.get("passed")
                is True
                for grader
                in executed_graders
            )
        )

        return {
            "case_id": case.id,
            "category": case.category,
            "title": case.title,
            "conversation_id": (
                conversation_id
            ),
            "passed": passed,
            "expected": case.expected,
            "actual": actual,
            "graders": grader_results,
            "turns": turn_results,
            "error": None,
        }

    except Exception as exc:
        try:
            session.rollback()
        except Exception:
            pass

        return {
            "case_id": case.id,
            "category": case.category,
            "title": case.title,
            "conversation_id": (
                conversation_id
            ),
            "passed": False,
            "expected": case.expected,
            "actual": {},
            "graders": [],
            "turns": turn_results,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": (
                    traceback.format_exc()
                ),
            },
        }

    finally:
        session.close()


# ============================================================
# Report
# ============================================================


def write_report(
    results: list[dict[str, Any]],
    output_path: Path,
) -> None:

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    passed = sum(
        1
        for result in results
        if result["passed"]
    )

    failed = len(results) - passed

    payload = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "summary": {
            "cases": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": (
                passed / len(results)
                if results
                else 0
            ),
        },
        "results": results,
    }

    output_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# Console
# ============================================================


def print_result(
    result: dict[str, Any],
) -> None:

    status = (
        "PASS"
        if result["passed"]
        else "FAIL"
    )

    print(
        f"[{status}] "
        f"{result['case_id']} "
        f"{result['title']}"
    )

    if result["passed"]:
        return

    if result.get("error"):
        error = result["error"]

        print(
            f"       ERROR: "
            f"{error['type']}: "
            f"{error['message']}"
        )

        return

    for grader in result.get(
        "graders",
        [],
    ):
        if grader.get("skipped"):
            continue

        if grader.get("passed"):
            continue

        for error in grader.get(
            "errors",
            [],
        ):
            print(
                f"       "
                f"{grader['grader']}: "
                f"{error}"
            )


# ============================================================
# CLI
# ============================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Teacher Agent Regression Eval"
        )
    )

    parser.add_argument(
        "--cases-file",
        type=Path,
        default=DEFAULT_CASE_FILE,
    )

    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help=(
            "只运行指定 Case。"
            "可重复传入。"
        ),
    )

    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        help=(
            "只运行指定 category。"
            "可重复传入。"
        ),
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    suite = load_eval_suite(
        args.cases_file
    )

    cases = filter_cases(
        suite,
        case_ids=(
            set(args.case_ids)
            if args.case_ids
            else None
        ),
        categories=(
            set(args.categories)
            if args.categories
            else None
        ),
    )

    if not cases:
        print("No eval cases selected.")
        return 1

    print()
    print(
        "Teacher Agent Regression Eval "
        f"({suite.name})"
    )
    print("=" * 60)

    results = []

    for case in cases:
        result = run_case(case)

        results.append(result)

        print_result(result)

    print("=" * 60)

    passed = sum(
        1
        for result in results
        if result["passed"]
    )

    total = len(results)
    failed = total - passed

    pass_rate = (
        passed / total * 100
        if total
        else 0
    )

    print(f"Cases:     {total}")
    print(f"Passed:    {passed}")
    print(f"Failed:    {failed}")
    print(
        f"Pass rate: {pass_rate:.1f}%"
    )

    write_report(
        results,
        args.report,
    )

    print()
    print(
        f"Report: {args.report}"
    )

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

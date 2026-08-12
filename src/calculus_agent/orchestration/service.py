from datetime import UTC, datetime
import json
from urllib.error import HTTPError, URLError

from sqlalchemy.orm import Session

from calculus_agent.models import AgentRun, ToolCallTrace
from calculus_agent.orchestration.agents import PaperAgentOrchestrator
from calculus_agent.orchestration.backend import BailianChatBackend
from calculus_agent.orchestration.types import AgentRunContext, RunBudget
from calculus_agent.requirements.parser import OpenAICompatibleRequirementParser
from calculus_agent.schemas import AgentRunRead, AgentRunRequest, ToolCallTraceRead


def run_paper_agent(
    session: Session,
    request: AgentRunRequest,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float,
) -> AgentRunRead:
    run = AgentRun(user_request=request.request, mode=request.mode, status="running")
    session.add(run)
    session.flush()
    context = AgentRunContext(session=session, budget=RunBudget(max_steps=request.max_steps))
    backend = BailianChatBackend(
        api_key=api_key, base_url=base_url, model=model, timeout=timeout
    )
    try:
        blueprint = None
        if request.mode == "multi_agent":
            blueprint = OpenAICompatibleRequirementParser(
                api_key=api_key, base_url=base_url, model=model, timeout=timeout
            ).parse(request.request)
        result = PaperAgentOrchestrator(backend).run(
            request.request,
            context,
            mode=request.mode,
            blueprint=blueprint,
        )
        run.status = result.status
        run.final_response = result.text
    except Exception as error:
        run.status = "failed"
        run.error_message = _friendly_error(error)
    run.steps_used = context.budget.steps_used
    run.completed_at = datetime.now(UTC)
    for trace in context.traces:
        session.add(
            ToolCallTrace(
                run_id=run.id,
                step=trace.step,
                actor=trace.actor,
                tool_name=trace.tool_name,
                arguments_json=trace.arguments,
                result_json=trace.result,
                status=trace.status,
                duration_ms=trace.duration_ms,
            )
        )
    session.flush()
    return AgentRunRead(
        run_id=run.id,
        status=run.status,
        mode=run.mode,
        final_response=run.final_response,
        steps_used=run.steps_used,
        error_message=run.error_message,
        current_paper=context.current_paper,
        traces=[
            ToolCallTraceRead(
                step=trace.step,
                actor=trace.actor,
                tool_name=trace.tool_name,
                arguments=trace.arguments,
                result=trace.result,
                status=trace.status,
                duration_ms=trace.duration_ms,
            )
            for trace in context.traces
        ],
    )


def _friendly_error(error: Exception) -> str:
    if isinstance(error, HTTPError):
        if error.code == 401:
            return "硅基流动 API Key 未提供或无效，请检查 .env 中的密钥配置"
        try:
            payload = json.loads(error.read().decode())
            detail = payload.get("error", {}).get("message")
        except (json.JSONDecodeError, UnicodeDecodeError):
            detail = None
        return f"硅基流动接口返回 HTTP {error.code}" + (f"：{detail}" if detail else "")
    if isinstance(error, URLError) or "Connection refused" in str(error):
        return "无法连接硅基流动模型服务，请检查网络和接口地址"
    if "timed out" in str(error).lower():
        return "硅基流动模型响应超时，请稍后重试或提高超时时间"
    return str(error)

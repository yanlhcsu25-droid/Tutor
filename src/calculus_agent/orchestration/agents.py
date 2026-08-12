import json
import time

from calculus_agent.orchestration.loop import ToolAgent
from calculus_agent.orchestration.tools import knowledge_tools, paper_tools, review_tools
from calculus_agent.orchestration.types import (
    AgentResult,
    AgentRunContext,
    AgentTool,
    ChatBackend,
    TraceEntry,
)
from calculus_agent.schemas import PaperBlueprint


SINGLE_AGENT_PROMPT = """你是单Agent组卷基线。你可以直接使用知识检索、题库供给、确定性组卷
和审核工具。教师明确给出的题量、总分、题型数量和知识点配额是硬约束，禁止擅自放宽。
必须基于工具结果回答，不得编造题目或题库供给。"""


class PaperAgentOrchestrator:
    def __init__(self, backend: ChatBackend) -> None:
        self.backend = backend

    def run(
        self,
        request: str,
        context: AgentRunContext,
        *,
        mode: str = "multi_agent",
        blueprint: PaperBlueprint | None = None,
    ) -> AgentResult:
        if mode == "single_agent":
            agent = ToolAgent(
                name="SinglePaperAgent",
                system_prompt=SINGLE_AGENT_PROMPT,
                backend=self.backend,
                tools=(knowledge_tools(context) + paper_tools(context) + review_tools(context)),
            )
            return agent.run(request, context)

        if blueprint is None:
            raise ValueError("多 Agent 模式缺少已解析的组卷蓝图")

        supply = self._execute_tool(
            context,
            actor="KnowledgeStewardAgent",
            tool=next(
                tool
                for tool in knowledge_tools(context)
                if tool.name == "inspect_question_supply"
            ),
            arguments={
                "knowledge_names": [item.name for item in blueprint.knowledge_quotas],
                "image_question_count": blueprint.image_question_count,
            },
        )
        paper = self._execute_tool(
            context,
            actor="PaperComposerAgent",
            tool=paper_tools(context)[0],
            arguments={"blueprint": blueprint.model_dump(mode="json")},
        )
        if context.current_paper is None:
            raise RuntimeError("组卷工具执行后未产生试卷")

        review = self._execute_tool(
            context,
            actor="PaperReviewerAgent",
            tool=review_tools(context)[0],
            arguments={},
        )
        type_supply = "、".join(
            f"{name}{count}道" for name, count in supply["by_question_type"].items()
        ) or "无"
        issue_summary = (
            "审核通过，无硬约束或答案解析问题"
            if review["status"] == "passed"
            else f"发现 {len(review['issues'])} 个问题"
        )
        return AgentResult(
            text=f"题库阶段：匹配知识点条件的题目共 {supply['total']} 道"
            f"（{type_supply}）。\n组卷阶段：已生成 {len(paper['items'])} 道题，"
            f"总分 {paper['total_score']} 分。\n审核阶段：{issue_summary}。"
            + (
                "\n缺口：" + "；".join(issue["message"] for issue in review["issues"])
                if review["issues"]
                else ""
            ),
            status=(
                "completed"
                if context.current_paper.feasible and review["status"] == "passed"
                else "needs_revision"
            ),
        )

    @staticmethod
    def _execute_tool(
        context: AgentRunContext, *, actor: str, tool: AgentTool, arguments: dict
    ) -> dict:
        signature = json.dumps([actor, tool.name, arguments], ensure_ascii=False, sort_keys=True)
        context.budget.consume_tool(signature)
        started = time.perf_counter()
        try:
            result = tool.handler(arguments)
        except Exception as error:
            result = {"error": str(error)}
            status = "error"
        else:
            status = "success"
        context.traces.append(
            TraceEntry(
                step=context.budget.steps_used,
                actor=actor,
                tool_name=tool.name,
                arguments=arguments,
                result=result,
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        )
        if status == "error":
            raise RuntimeError(f"{actor} 执行 {tool.name} 失败：{result['error']}")
        return result

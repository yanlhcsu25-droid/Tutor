import json

from calculus_agent.orchestration.types import ChatBackend
from calculus_agent.question_types import canonical_question_type
from calculus_agent.requirements.parser import (
    _normalize_blueprint_payload,
    _strip_json_fence,
)
from calculus_agent.schemas import PaperBlueprint, PaperPreviewRead, SectionRequirement


SYSTEM_PROMPT = """你是有状态的高等数学组卷对话 Agent。你的职责是读取当前蓝图和当前试卷，
理解教师本轮是在重述、修改还是要求澄清，并输出受控 JSON；禁止直接选择或编造题目。

仅输出以下两种 JSON 之一：
1. 可以确定修改时：
{"action":"update","message":"简短说明修改结果","operations":[受控操作]}
2. 信息不足、前后冲突或无法唯一理解时：
{"action":"clarify","message":"向教师提出一个具体、简短的问题"}

规则：
- 必须以当前蓝图为基础，只输出教师明确要求的操作。
- 用户完整重述题型分布时，按完整方案更新；不得要求必须出现“增加/减少”。
- 题型数量与 sections 一致；每段 total_score=count*score_per_question；总题数和总分取各段之和。
- “保持总分”必须重新合理分配主观题分值，不得产生小数总分。
- 当前试卷中的 slot 从 1 开始。要求“第N题保留/锁定”时，将对应 question_id 加入 locked_question_ids。
- 要求“第N题换掉”时，将对应 question_id 加入 excluded_question_ids，并从 locked_question_ids 移除。
- 要求指定题目分值时，写入 score_overrides，键是 question_id。
- 未被要求修改的锁定题、排除题、题序、知识点要求和种子必须保留。
- 若用户只是重述当前方案且没有变化，返回 update，message 说明方案未变化。
- 不得输出 Markdown 或 JSON 之外的文字。"""

PATCH_OPERATIONS = """
受控操作格式：
- 完整重设题型：{"action":"set_sections","sections":[{"question_type":"选择题","count":2,"score_per_question":5,"total_score":10}]}
- 增减题型：{"action":"adjust_question_type","question_type":"计算题","delta":-1}
- 锁定题目：{"action":"lock_question","slot":3}
- 解锁题目：{"action":"unlock_question","slot":3}
- 换掉题目：{"action":"replace_question","slot":3}
- 修改题目分值：{"action":"update_score","slot":3,"score":10}
- 设置重点知识点：{"action":"set_knowledge_preferences","names":["函数的极限"]}
- 设置标题：{"action":"set_title","title":"函数与极限章节练习"}
若多项修改同时发生，按执行顺序输出多个 operations。
"""


class PaperConversationAgent:
    def __init__(self, backend: ChatBackend) -> None:
        self.backend = backend

    def decide(
        self,
        requirement: str,
        blueprint: PaperBlueprint,
        *,
        paper: PaperPreviewRead | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, str, PaperBlueprint | None]:
        state = {
            "blueprint": blueprint.model_dump(mode="json"),
            "paper": paper.model_dump(mode="json") if paper is not None else None,
        }
        messages = [{"role": "system", "content": SYSTEM_PROMPT + PATCH_OPERATIONS}]
        for turn in (history or [])[-8:]:
            if turn.get("role") in {"user", "assistant"} and turn.get("content"):
                messages.append(
                    {"role": turn["role"], "content": str(turn["content"])[:2000]}
                )
        messages.append(
            {
                "role": "user",
                "content": "当前状态：\n"
                + json.dumps(state, ensure_ascii=False)
                + "\n\n教师本轮要求：\n"
                + requirement,
            }
        )
        response = self.backend.complete(messages, [])
        content = response.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Agent 未返回有效内容")
        payload = json.loads(_strip_json_fence(content))
        action = payload.get("action")
        message = str(payload.get("message") or "").strip()
        if action == "clarify":
            return action, message or "请补充说明希望修改的内容。", None
        if action != "update" or not isinstance(payload.get("operations"), list):
            raise ValueError("Agent 返回的操作格式无效")
        updated = apply_paper_patch(blueprint, payload["operations"], paper=paper)
        return action, message or "已根据当前试卷更新组卷方案。", updated


def apply_paper_patch(
    blueprint: PaperBlueprint,
    operations: list[dict],
    *,
    paper: PaperPreviewRead | None = None,
) -> PaperBlueprint:
    """Apply the Agent's allow-listed operations without granting database access."""
    state = blueprint.model_dump(mode="json")
    sections = {item.question_type: item.model_dump() for item in blueprint.sections}

    def question_id(operation: dict) -> str:
        explicit = operation.get("question_id")
        if explicit:
            return str(explicit)
        slot = operation.get("slot")
        if paper is None or not isinstance(slot, int) or not 1 <= slot <= len(paper.items):
            raise ValueError("题号操作需要有效的当前试卷和 slot")
        return paper.items[slot - 1].question_id

    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError("PaperPatch operation 必须是对象")
        action = operation.get("action")
        if action == "set_sections":
            raw_sections = operation.get("sections")
            if not isinstance(raw_sections, list) or not raw_sections:
                raise ValueError("set_sections 需要非空 sections")
            parsed = [SectionRequirement.model_validate(item) for item in raw_sections]
            sections = {item.question_type: item.model_dump() for item in parsed}
        elif action == "adjust_question_type":
            question_type = canonical_question_type(str(operation.get("question_type") or ""))
            delta = operation.get("delta")
            if not isinstance(delta, int) or delta == 0:
                raise ValueError("adjust_question_type 需要非零整数 delta")
            current = sections.get(question_type)
            count = (current["count"] if current else 0) + delta
            if count <= 0:
                sections.pop(question_type, None)
            else:
                score = current["score_per_question"] if current else (
                    5 if question_type in {"选择题", "填空题"} else 10
                )
                sections[question_type] = {
                    "question_type": question_type,
                    "count": count,
                    "score_per_question": score,
                    "total_score": count * score,
                }
        elif action in {"lock_question", "unlock_question", "replace_question"}:
            qid = question_id(operation)
            locked = list(state["locked_question_ids"])
            excluded = list(state["excluded_question_ids"])
            if action == "lock_question" and qid not in locked:
                locked.append(qid)
            if action in {"unlock_question", "replace_question"}:
                locked = [item for item in locked if item != qid]
            if action == "replace_question" and qid not in excluded:
                excluded.append(qid)
            state["locked_question_ids"] = locked
            state["excluded_question_ids"] = excluded
        elif action == "update_score":
            qid = question_id(operation)
            score = operation.get("score")
            if not isinstance(score, (int, float)) or score <= 0:
                raise ValueError("update_score 需要正数 score")
            state["score_overrides"] = {**state["score_overrides"], qid: score}
        elif action == "set_knowledge_preferences":
            names = operation.get("names")
            if not isinstance(names, list):
                raise ValueError("set_knowledge_preferences 需要 names 数组")
            state["soft_knowledge_preferences"] = [str(name) for name in names if str(name).strip()]
        elif action == "set_title":
            title = str(operation.get("title") or "").strip()
            if not title:
                raise ValueError("set_title 需要 title")
            state["title"] = title
        else:
            raise ValueError(f"不支持的 PaperPatch 操作：{action}")

    if not sections:
        raise ValueError("修改后试卷不能没有题型")
    state["sections"] = list(sections.values())
    return PaperBlueprint.model_validate(_normalize_blueprint_payload(state))

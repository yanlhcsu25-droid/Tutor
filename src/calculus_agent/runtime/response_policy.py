"""Deterministic response acceptance and boundary classification."""

import re
from typing import Any


_PAPER_CANDIDATE_BLOCKERS = {
    "same_difficulty_candidate_not_found",
    "target_difficulty_candidate_not_found",
    "target_difficulty_same_as_current",
    "no_easier_candidate",
    "no_harder_candidate",
    "replacement_candidate_not_found",
    "add_question_candidate_not_found",
}


class ResponsePolicy:
    @staticmethod
    def paper_candidate_blocker(payload: dict[str, Any]) -> str | None:
        codes = [payload.get("code"), *(payload.get("blocking_errors") or [])]
        return next((str(code) for code in codes if code in _PAPER_CANDIDATE_BLOCKERS), None)

    @staticmethod
    def paper_candidate_failure_message(code: str, *, position: int | None) -> str:
        target = f"全卷第{position}题" if position else "目标题目"
        choices = {
            "same_difficulty_candidate_not_found": "是否允许尝试难度稍低或稍高的题目？",
            "no_easier_candidate": "是否允许尝试同难度或难度稍高的题目？",
            "no_harder_candidate": "是否允许尝试同难度或难度稍低的题目？",
            "target_difficulty_same_as_current": "请指定不同的目标难度，或允许保持当前难度重新选择。",
            "target_difficulty_candidate_not_found": "是否允许调整目标难度后再尝试？",
            "add_question_candidate_not_found": "是否允许调整题型或试卷范围后再尝试？",
            "replacement_candidate_not_found": "是否允许调整难度要求后再尝试？",
        }
        reason = (
            "当前没有找到满足本卷约束的同难度替换题"
            if code == "same_difficulty_candidate_not_found"
            else "当前没有找到满足本卷约束的可用候选题"
        )
        return (
            f"已定位到{target}，但{reason}。{choices[code]}"
            "在你确认之前，我不会修改试卷。"
        )

    @staticmethod
    def pending_generation_pause_intent(message: str) -> bool:
        """Distinguish keeping a pending plan from explicitly discarding it."""
        normalized = message.strip()
        pause = re.search(
            r"先别(?:生成|确认|执行)|暂时(?:别|不要)(?:生成|确认|执行)|"
            r"不要现在生成|先暂停|先放着|再想想|稍后确认|等我(?:后面|之后)?确认|"
            r"保留当前方案|不要清掉方案|不要取消",
            normalized,
        )
        explicit_cancel = re.search(
            r"取消(?:这个|当前)?方案|放弃(?:这个|当前)?方案|"
            r"这个方案不要了|不要这个方案|算了|作废",
            normalized,
        )
        executable_patch = re.search(
            r"改成|改为|调整|降到|提高到|增加|减少|"
            r"(?:题数|题量|总分|章节|范围|题型|分值).{0,8}(?:改|变|调整)",
            normalized,
        )
        return bool(pause and not explicit_cancel and not executable_patch)

    @staticmethod
    def explicitly_requests_generation(message: str) -> bool:
        """Deterministic safety net when semantic routing under-classifies a paper request."""
        return any(
            word in message
            for word in (
                "出一套", "出一份卷", "出卷", "组一份卷", "生成试卷", "组卷",
                "测试卷", "练习卷", "测验卷", "考试卷",
            )
        ) or bool(
            re.search(r"(?:出|生成|组).{0,12}(?:试卷|卷子|测试|测验|考试)", message)
        )

    """Classify a model response; callers own retries and message mutation."""

    @staticmethod
    def contains_leaked_tool_protocol(content: str) -> bool:
        return any(marker in content for marker in (
            "</think>", "<tool_call", "</tool_call>", "<arg_key>", "<arg_value>",
        ))

    @staticmethod
    def paper_change_lifecycle_observed(trace_calls: list[dict[str, Any]]) -> bool:
        return any(call["tool_name"] in {
            "preview_paper_changes", "confirm_paper_changes", "discard_pending_plan",
            "operate_paper_version",
        } for call in trace_calls)

    @classmethod
    def requires_pending_paper_change_recheck(
        cls, *, pending_adjustment: bool, trace_calls: list[dict[str, Any]], already_rechecked: bool,
    ) -> bool:
        return pending_adjustment and bool(trace_calls) and not cls.paper_change_lifecycle_observed(trace_calls) and not already_rechecked

    @staticmethod
    def paper_change_intent(message: str, *, paper_state_at_turn_start: bool = True) -> bool:
        if not paper_state_at_turn_start:
            return False
        for clause in re.split(r"[，。；;！？]", message):
            if not (
                re.search(r"(?:换|替换|删除|删掉|移除|修改|调整)", clause)
                and re.search(r"(?:题|题目|分值|分数)", clause)
            ):
                continue
            if re.search(
                r"(?:不要|别|无需|不需要|禁止)[^，。；;！？]{0,8}"
                r"(?:换|替换|删除|删掉|移除|修改|调整)",
                clause,
            ):
                continue
            if re.search(
                r"(?:只|仅)?(?:查看|看看|检查|展示|读取)[^，。；;！？]{0,8}预览",
                clause,
            ):
                continue
            return True
        return False

    @staticmethod
    def question_type_summary_query(message: str) -> str | None:
        match = re.search(r"(选择题|填空题|计算题|证明题|解答题)", message)
        if match is None:
            return None
        if not (
            re.search(r"(?:有没(?:有)?|是否有)", message)
            or re.search(r"(?:有|共|总共|一共)?\s*(?:几|多少)\s*道?", message)
            or re.search(r"(?:数量|题数)", message)
        ):
            return None
        return "计算题" if match.group(1) == "解答题" else match.group(1)

    @staticmethod
    def contextual_replacement_intent(message: str) -> bool:
        """Recognize a replacement whose target is supplied by validated focus."""
        focus = r"(?:这道题|这题|刚才(?:读的|看的|说的)?那道?题|刚刚(?:读的|看的)?那道?题)"
        action = r"(?:换掉|替换(?:掉|一下)?|换(?:一下|一个|一道|成))"
        return bool(
            (
                re.search(rf"{focus}[^。！？]{{0,8}}{action}", message)
                or re.search(rf"{action}[^。！？]{{0,8}}{focus}", message)
            )
            and not re.search(r"(?:不要|别|无需|不需要)[^。！？]{0,6}(?:换|替换)", message)
        )

    @staticmethod
    def successful_observation(trace_calls: list[dict[str, Any]], tool_name: str) -> bool:
        return any(
            call["tool_name"] == tool_name and (call.get("result") or {}).get("ok")
            for call in trace_calls
        )

    @staticmethod
    def requires_post_inspection_recheck(
        *, has_current_paper: bool, pending: bool, pending_adjustment: bool,
        pending_generation: bool, recheck_count: int, max_rechecks: int,
        trace_calls: list[dict[str, Any]], environment_tool_names: list[str],
        design_tool_names: list[str], is_teaching_design_tool: Any,
    ) -> bool:
        return (
            not has_current_paper and not pending and not pending_adjustment
            and not pending_generation and recheck_count < max_rechecks
            and any(call["tool_name"] in environment_tool_names for call in trace_calls)
            and not any(call["tool_name"] == "prepare_generation_plan" for call in trace_calls)
            and not any(call["tool_name"] in design_tool_names for call in trace_calls)
            and not any(is_teaching_design_tool(call["tool_name"]) for call in trace_calls)
        )

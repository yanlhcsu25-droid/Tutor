import json
import re
from math import isclose
from urllib.request import Request, urlopen

from calculus_agent.question_types import PAPER_QUESTION_TYPES, canonical_question_type
from calculus_agent.schemas import PaperBlueprint


PAPER_TYPE_PROMPT = "、".join(PAPER_QUESTION_TYPES)
KNOWLEDGE_PROMPT = (
    "knowledge_quotas只记录教师明确给出数量的硬约束；"
    "重点覆盖、重点考察、主要考察、优先等表达写入soft_knowledge_preferences，禁止编造题数。"
)


class OllamaRequirementParser:
    def __init__(self, *, base_url: str, model: str, timeout: float = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def parse(self, requirement: str) -> PaperBlueprint:
        prompt = (
            "你是高等数学组卷需求解析器。把教师要求转换为严格JSON，不要选择题目。"
            "字段：title, total_questions, total_score, sections, knowledge_quotas,"
            "soft_knowledge_preferences, seed。"
            "sections必须为{question_type,count,score_per_question,total_score}数组，"
            "且每部分total_score=count*score_per_question，所有部分题数与分值分别等于全卷题数与总分。"
            "另有image_question_count表示至少需要的带图片题数量，strict_knowledge表示是否禁止用无关知识点补题。"
            "knowledge_quotas是{name,count}数组。未提及总分时为100，"
            f"未提及title时为\"高等数学测试卷\"，未提及seed时为42。"
            f"题型只能写{PAPER_TYPE_PROMPT}。{KNOWLEDGE_PROMPT}\n"
            f"教师要求：{requirement}"
        )
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "format": PaperBlueprint.model_json_schema(),
                "options": {"temperature": 0},
            }
        ).encode()
        request = Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode())
        blueprint = PaperBlueprint.model_validate(
            _normalize_blueprint_payload(json.loads(body["response"]))
        )
        return apply_explicit_constraints(requirement, blueprint)


class OpenAICompatibleRequirementParser:
    def __init__(
        self, *, api_key: str, base_url: str, model: str, timeout: float = 120
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def parse(self, requirement: str) -> PaperBlueprint:
        prompt = (
            "你是高等数学组卷需求解析器。把教师要求转换为严格JSON，不要选择题目。"
            "字段：title, total_questions, total_score, sections, knowledge_quotas,"
            "soft_knowledge_preferences, seed。"
            "sections必须为{question_type,count,score_per_question,total_score}数组，"
            "且每部分total_score=count*score_per_question，所有部分题数与分值分别等于全卷题数与总分。"
            "另有image_question_count表示至少需要的带图片题数量，strict_knowledge表示是否禁止用无关知识点补题。"
            "knowledge_quotas是{name,count}数组。未提及总分时为100，"
            f"未提及title时为\"高等数学测试卷\"，未提及seed时为42。"
            f"题型只能写{PAPER_TYPE_PROMPT}。{KNOWLEDGE_PROMPT}\n"
            f"教师要求：{requirement}"
        )
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "enable_thinking": False,
                "temperature": 0,
            },
            ensure_ascii=False,
        ).encode()
        request = Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode())
        content = body["choices"][0]["message"]["content"]
        parsed = _normalize_blueprint_payload(json.loads(_strip_json_fence(content)))
        grade = parsed.get("grade")
        if isinstance(grade, int):
            parsed["grade"] = {7: "七年级", 8: "八年级", 9: "九年级"}.get(
                grade, str(grade)
            )
        blueprint = PaperBlueprint.model_validate(parsed)
        return apply_explicit_constraints(requirement, blueprint)


# 兼容旧导入；生产路由使用语义更准确的 OpenAICompatibleRequirementParser。
BailianRequirementParser = OpenAICompatibleRequirementParser


def _strip_json_fence(value: str) -> str:
    value = value.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.DOTALL)
    if match:
        return match.group(1)
    start = value.find("{")
    end = value.rfind("}")
    return value[start : end + 1] if start >= 0 and end > start else value


_TOPIC_ALIASES = {
    "函数极限": "函数的极限",
    "极限": "函数的极限",
    "无穷小": "无穷小与无穷大",
    "重要极限": "极限存在准则 两个重要极限",
    "两个重要极限": "极限存在准则 两个重要极限",
}


def _normalize_topic_names(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in values:
        value = re.sub(r"^(?:重点|主要|优先)?(?:覆盖|考察)?[:：\s]*", "", raw).strip()
        value = value.strip("-—•* \t")
        if not value:
            continue
        canonical = _TOPIC_ALIASES.get(value, value)
        if canonical not in normalized:
            normalized.append(canonical)
    return normalized


def _normalize_blueprint_payload(payload: dict) -> dict:
    """Repair common type drift in otherwise valid model-produced JSON."""
    normalized = dict(payload)
    preferences = normalized.get("soft_knowledge_preferences")
    if isinstance(preferences, str):
        parts = [
            item
            for item in re.split(r"[,，、;；\n]|\s+-\s+", preferences)
            if item.strip()
        ]
        normalized["soft_knowledge_preferences"] = _normalize_topic_names(parts)
    elif isinstance(preferences, list):
        normalized["soft_knowledge_preferences"] = _normalize_topic_names(
            [str(item) for item in preferences]
        )
    elif preferences is None:
        normalized["soft_knowledge_preferences"] = []
    return normalized


def apply_explicit_constraints(requirement: str, blueprint: PaperBlueprint) -> PaperBlueprint:
    """Let explicit teacher wording override fields occasionally omitted by the model."""
    updates = {}
    topic = (
        r"[\u4e00-\u9fff]{0,16}"
        r"(?:函数|方程|极限|导数|积分|微分|级数|定理|公式|无穷小|运算法则)"
    )
    quota_pairs = re.findall(
        rf"(?P<name>{topic})\s*(?:至少|不少于|出|必须(?:出)?)?\s*"
        rf"(?P<count>\d+)\s*(?:道|题)",
        requirement,
    )
    quota_pairs.extend(
        (name, count)
        for count, name in re.findall(
            rf"(?:至少|不少于)\s*(\d+)\s*(?:道|题)\s*({topic})",
            requirement,
        )
    )
    explicit_quotas = [
        {"name": name, "count": int(count)}
        for name, count in dict(quota_pairs).items()
    ]
    if explicit_quotas:
        updates["knowledge_quotas"] = explicit_quotas
        updates["soft_knowledge_preferences"] = []
        updates["strict_knowledge"] = False
    elif re.search(r"重点(?:覆盖|考察)|主要考|优先", requirement):
        # Models often turn a qualitative emphasis into invented 4/3/3 quotas.
        # Preserve the names, but explicitly discard those fabricated counts.
        model_topics = [item.name for item in blueprint.knowledge_quotas]
        if not model_topics:
            model_topics = blueprint.soft_knowledge_preferences
        updates["soft_knowledge_preferences"] = _normalize_topic_names(model_topics)
        updates["knowledge_quotas"] = []
        updates["strict_knowledge"] = False
    image_count = re.search(r"(?:带|含|需要)\s*图片(?:的题目)?\s*(\d+)\s*题", requirement)
    if image_count:
        updates["image_question_count"] = int(image_count.group(1))
    elif re.search(r"(?:需要|要求|必须).*图片|含图片", requirement):
        updates["image_question_count"] = 1
    sections = []
    for question_type in PAPER_QUESTION_TYPES:
        formula = re.search(
            rf"{question_type}\s*(\d+)\s*(?:道|题)\s*[×xX*]\s*"
            rf"(\d+(?:\.\d+)?)\s*分(?:\s*=\s*(\d+(?:\.\d+)?)\s*分)?",
            requirement,
        )
        per_item = re.search(
            rf"{question_type}\s*(\d+)\s*(?:道|题)[^；;。\n]*?每(?:小)?题\s*(\d+(?:\.\d+)?)\s*分",
            requirement,
        )
        total = re.search(
            rf"{question_type}\s*(\d+)\s*(?:道|题)[^；;。]*?(?:共|合计)\s*(\d+)\s*分",
            requirement,
        )
        if formula:
            count = int(formula.group(1))
            score = float(formula.group(2))
        elif per_item:
            count = int(per_item.group(1))
            score = float(per_item.group(2))
        elif total:
            count, section_total = map(int, total.groups())
            if count == 0:
                continue
            score = section_total / count
        else:
            continue
        sections.append(
            {
                "question_type": question_type,
                "count": count,
                "score_per_question": score,
                "total_score": count * score,
            }
        )
    if sections:
        updates["sections"] = sections
        updates["question_type_counts"] = {
            section["question_type"]: section["count"] for section in sections
        }
        updates["total_questions"] = sum(section["count"] for section in sections)
        updates["total_score"] = sum(section["total_score"] for section in sections)
    final_sections = updates.get("sections") or [
        section.model_dump() for section in blueprint.sections
    ]
    explicit_total = re.search(
        r"(?:满分|(?:题目)?总分)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*分?",
        requirement,
    )
    target_total = float(explicit_total.group(1)) if explicit_total else 100
    if final_sections and not isclose(
        sum(section["total_score"] for section in final_sections), target_total
    ):
        by_type = {section["question_type"]: dict(section) for section in final_sections}
        _rebalance_subjective_scores(by_type, target_total)
        updates["sections"] = list(by_type.values())
        updates["total_score"] = target_total
    return PaperBlueprint.model_validate({**blueprint.model_dump(), **updates})


_TYPE_WORD = r"选择题?|多选题?|填空题?|计算题?|证明题?|问答题?|解答题?"
_OBJECTIVE_TYPES = {"选择题", "多选题", "填空题"}


def _type_from_text(value: str) -> str:
    return canonical_question_type(value if value.endswith("题") else f"{value}题")


def _default_score(blueprint: PaperBlueprint, question_type: str) -> float:
    by_type = {section.question_type: section.score_per_question for section in blueprint.sections}
    if question_type in by_type:
        return by_type[question_type]
    return 5.0 if question_type in _OBJECTIVE_TYPES else 10.0


def _hard_total_score(requirement: str, base_blueprint: PaperBlueprint) -> int | None:
    explicit = re.search(
        r"(?:保持\s*(?:总分)?|总分\s*(?:保持|仍为|维持))\s*(\d+)\s*分",
        requirement,
    )
    if explicit:
        return int(explicit.group(1))
    if re.search(r"(?:保持总分|总分保持|总分维持)\s*不变", requirement):
        return base_blueprint.total_score
    return None


def _rebalance_subjective_scores(
    sections: dict[str, dict], hard_total: int
) -> None:
    objective_total = sum(
        section["total_score"]
        for question_type, section in sections.items()
        if question_type in _OBJECTIVE_TYPES
    )
    subjective = [
        section for question_type, section in sections.items()
        if question_type not in _OBJECTIVE_TYPES
    ]
    subjective_count = sum(section["count"] for section in subjective)
    remaining = hard_total - objective_total
    if subjective_count <= 0 or remaining <= 0:
        raise ValueError("固定客观题分值后，没有可分配给主观题的剩余分值")
    per_item = remaining / subjective_count
    for section in subjective:
        section["score_per_question"] = per_item
        section["total_score"] = section["count"] * per_item


def apply_blueprint_modification(
    requirement: str, base_blueprint: PaperBlueprint
) -> PaperBlueprint:
    """Apply explicit conversational deltas while preserving untouched sections."""
    sections = {
        section.question_type: section.model_dump()
        for section in base_blueprint.sections
    }

    def change(question_type: str, delta: int, *, score: float | None = None) -> None:
        current = sections.get(question_type)
        next_count = (current["count"] if current else 0) + delta
        if next_count <= 0:
            sections.pop(question_type, None)
            return
        per_item = current["score_per_question"] if current else (
            score if score is not None else _default_score(base_blueprint, question_type)
        )
        sections[question_type] = {
            "question_type": question_type,
            "count": next_count,
            "score_per_question": per_item,
            "total_score": next_count * per_item,
        }

    replacement = re.search(
        rf"把\s*(\d+)\s*(?:道|题)?\s*({_TYPE_WORD})\s*"
        rf"(?:换成|替换为|改成)\s*(\d+)\s*(?:道|题)?\s*({_TYPE_WORD})",
        requirement,
    )
    if replacement:
        old_count, old_type, new_count, new_type = replacement.groups()
        source_type = _type_from_text(old_type)
        target_type = _type_from_text(new_type)
        source_score = sections.get(source_type, {}).get("score_per_question")
        change(source_type, -int(old_count))
        change(target_type, int(new_count), score=source_score)
    else:
        operation = re.search(
            rf"(加入|增加|添加|减少|删去|删除)\s*(\d+)\s*(?:道|题)?\s*({_TYPE_WORD})",
            requirement,
        )
        type_first = False
        if not operation:
            operation = re.search(
                rf"(加入|增加|添加|减少|删去|删除)\s*({_TYPE_WORD})\s*(\d+)\s*(?:道|题)?",
                requirement,
            )
            type_first = operation is not None
        if not operation:
            raise ValueError("未识别到明确的题型增减要求")
        if type_first:
            action, question_type, count = operation.groups()
        else:
            action, count, question_type = operation.groups()
        delta = int(count) * (-1 if action in {"减少", "删去", "删除"} else 1)
        change(_type_from_text(question_type), delta)

    if not sections:
        raise ValueError("修改后试卷不能没有题目")
    hard_total = _hard_total_score(requirement, base_blueprint)
    if hard_total is not None:
        _rebalance_subjective_scores(sections, hard_total)
    return PaperBlueprint.model_validate({
        **base_blueprint.model_dump(),
        **({"total_score": hard_total} if hard_total is not None else {}),
        "sections": list(sections.values()),
    })

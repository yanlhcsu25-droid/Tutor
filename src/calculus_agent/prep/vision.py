import json
import re
from urllib.request import Request, urlopen

from calculus_agent.schemas import VisionQuestionExtractRead

_ALLOWED_IMAGE_PREFIXES = (
    "data:image/jpeg;base64,",
    "data:image/png;base64,",
    "data:image/webp;base64,",
)


class BailianVisionExtractor:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def extract(
        self, question_image: str, solution_image: str | None = None
    ) -> VisionQuestionExtractRead:
        _validate_image(question_image)
        if solution_image:
            _validate_image(solution_image)
        content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "识别标准印刷数学题图片，严格返回JSON。保留数学公式并使用$...$ LaTeX。"
                    "不要补写图片中不存在的信息。字段必须为question_text、options、"
                    "question_type、final_answer、solution_text、knowledge_names、warnings。"
                    "question_type只能是选择题、填空题、计算题或证明题；无法确认的答案或解析填空字符串，"
                    "并在warnings说明。第一张图是题目，若有第二张图则是答案或解析。"
                ),
            },
            {"type": "image_url", "image_url": {"url": question_image}},
        ]
        if solution_image:
            content.append({"type": "image_url", "image_url": {"url": solution_image}})
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": content}],
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
        content_text = body["choices"][0]["message"]["content"]
        parsed = json.loads(_strip_json_fence(content_text))
        return VisionQuestionExtractRead(
            question_text=str(parsed.get("question_text") or "").strip(),
            options=[str(item).strip() for item in parsed.get("options") or [] if str(item).strip()],
            question_type=str(parsed.get("question_type") or "计算题").strip(),
            final_answer=str(parsed.get("final_answer") or "").strip(),
            solution_text=str(parsed.get("solution_text") or "").strip(),
            knowledge_names=[
                str(item).strip()
                for item in parsed.get("knowledge_names") or []
                if str(item).strip()
            ],
            needs_review=True,
            warnings=[str(item) for item in parsed.get("warnings") or []],
        )


def _validate_image(value: str) -> None:
    if not value.startswith(_ALLOWED_IMAGE_PREFIXES):
        raise ValueError("仅支持 JPG、PNG 或 WebP 图片")
    if len(value) > 14_000_000:
        raise ValueError("单张图片不能超过约 10 MB")


def _strip_json_fence(value: str) -> str:
    value = value.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.DOTALL)
    if match:
        return match.group(1)
    start = value.find("{")
    end = value.rfind("}")
    return value[start : end + 1] if start >= 0 and end > start else value

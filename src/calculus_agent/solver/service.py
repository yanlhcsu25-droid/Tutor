import json
from typing import Protocol
from urllib.request import Request, urlopen

from calculus_agent.schemas import SolverResult


class Solver(Protocol):
    def solve(self, question: str, knowledge_candidates: list[str]) -> SolverResult: ...


class ReferenceAnswerSolver:
    """Deterministic baseline used for ingestion and integration testing."""

    def __init__(self, reference_answer: str) -> None:
        self.reference_answer = reference_answer

    def solve(self, question: str, knowledge_candidates: list[str]) -> SolverResult:
        return SolverResult(
            solution_steps=["使用数据集参考答案作为基线；尚未生成推导过程。"],
            final_answer=self.reference_answer,
            used_knowledge=knowledge_candidates[:3],
            used_methods=[],
            model_name="reference-baseline",
        )


class OllamaSolver:
    def __init__(self, *, base_url: str, model: str, timeout: float = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def solve(self, question: str, knowledge_candidates: list[str]) -> SolverResult:
        prompt = (
            "你是中文初中数学教师。请给出符合考试书写规范、可核验且简洁的标准解答。"
            "只返回JSON，字段为solution_steps、final_answer、used_knowledge、used_methods。"
            "used_knowledge优先从候选知识点中选择，不要创造近义标签。\n"
            f"候选知识点：{knowledge_candidates}\n题目：{question}"
        )
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "format": SolverResult.model_json_schema(),
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
        result = json.loads(body["response"])
        return SolverResult(model_name=self.model, **result)


class OpenAICompatibleSolver:
    """用于 SiliconFlow 等 OpenAI 兼容端点的解题器。"""

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: float = 120) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def solve(self, question: str, knowledge_candidates: list[str]) -> SolverResult:
        prompt = (
            "你是高等数学教师。请给出可核验且简洁的标准解答，只返回JSON。"
            "字段为solution_steps、final_answer、used_knowledge、used_methods。"
            "used_knowledge只能从候选知识点中选择，不得创造新标签。\n"
            f"候选知识点：{knowledge_candidates}\n题目：{question}"
        )
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }, ensure_ascii=False).encode()
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
        start, end = content.find("{"), content.rfind("}")
        parsed = json.loads(content[start:end + 1] if start >= 0 and end > start else content)
        return SolverResult(model_name=self.model, **parsed)

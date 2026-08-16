"""Run the phase-one 20-question real-model calibration set.

Usage:
  CALCULUS_AGENT_KNOWLEDGE_LLM_ENABLED=true uv run python \
    scripts/validate_knowledge_classification_llm.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from calculus_agent.db import Base
from calculus_agent.knowledge.classification import (
    build_knowledge_llm_backend,
    classify_text_with_llm,
    ensure_calculus_taxonomy,
)


CASES = [
    ("函数极限-多项式", r"求 $\lim_{x\to1}(x^2-1)/(x-1)$", "约分后取极限", ["函数极限"]),
    ("函数极限-指数", r"求 $\lim_{x\to0}(e^x-1)/x$", "利用重要极限", ["两个重要极限", "函数极限"]),
    ("数列极限", r"求 $\lim_{n\to\infty}(1+1/n)^n$", "使用第二个重要极限", ["两个重要极限", "数列极限"]),
    ("两个重要极限-sin", r"求 $\lim_{x\to0}\sin x/x$", "使用第一个重要极限", ["两个重要极限"]),
    ("两个重要极限-e", r"求 $\lim_{n\to\infty}(1+2/n)^n$", "化为第二个重要极限", ["两个重要极限"]),
    ("洛必达-0比0", r"求 $\lim_{x\to0}(e^x-1-x)/x^2$", "分子分母求导两次", ["洛必达法则", "函数极限"]),
    ("洛必达-无穷比无穷", r"求 $\lim_{x\to\infty}\ln x/x$", "使用洛必达法则", ["洛必达法则", "函数极限"]),
    ("导数定义-求值", r"已知 $f(x)=x^2$，用定义求 $f'(1)$", "由差商极限定义计算", ["导数定义"]),
    ("导数定义-可导", "根据导数定义判断 |x| 在0处是否可导", "比较左右差商极限", ["导数定义", "左右极限"]),
    ("复合函数求导", r"求 $y=\sin(x^2)$ 的导数", "应用链式法则", ["复合函数求导"]),
    ("复合函数多层", r"求 $y=e^{\sin x}$ 的导数", "逐层使用链式法则", ["复合函数求导"]),
    ("泰勒公式", "写出 ln(1+x) 在 x=0 处的三阶展开", "使用麦克劳林展开", ["泰勒公式"]),
    ("泰勒求极限", r"求 $\lim_{x\to0}(\sin x-x)/x^3$", "将 sin x 作泰勒展开", ["泰勒公式", "函数极限"]),
    ("不定积分-基本", r"求 $\int x^2 dx$", "使用基本积分公式", ["不定积分"]),
    ("定积分-基本", r"计算 $\int_0^1 x^2 dx$", "由牛顿-莱布尼茨公式代入上下限", ["定积分"]),
    ("换元积分-根式", r"求 $\int x/\sqrt{1+x^2}\,dx$", "令 u=1+x^2", ["换元积分法", "不定积分"]),
    ("换元积分-三角", r"求 $\int \cos(2x+1)dx$", "令 u=2x+1", ["换元积分法", "不定积分"]),
    ("分部积分-xex", r"求 $\int xe^x dx$", "使用分部积分法", ["分部积分法", "不定积分"]),
    ("分部积分-lnx", r"求 $\int \ln x dx$", "取 u=ln x，dv=dx，分部积分", ["分部积分法", "不定积分"]),
    ("综合-泰勒极限", r"求 $\lim_{x\to0}(e^x-1-x)/x^2$，不得使用洛必达", "用 e^x 的泰勒公式", ["泰勒公式", "函数极限"]),
]


class RecordingBackend:
    """Capture response shape for local benchmark diagnostics; never captures headers."""

    def __init__(self, backend) -> None:
        self.backend = backend
        self.last_response = None

    def complete(self, messages, tools, **kwargs):
        self.last_response = None
        try:
            self.last_response = self.backend.complete(messages, tools, **kwargs)
            return self.last_response
        except HTTPError as error:
            self.last_response = {
                "http_status": error.code,
                "error_body": error.read().decode("utf-8", errors="replace")[:2000],
            }
            raise
        except Exception as error:
            self.last_response = {
                "client_error_type": type(error).__name__,
                "client_error_message": str(error)[:2000],
            }
            raise


def _raw_response_diagnostic(response) -> dict:
    if not isinstance(response, dict):
        return {"response_type": type(response).__name__}
    if "http_status" in response:
        return {
            "response_type": "http_error",
            "http_status": response["http_status"],
            "error_body": response.get("error_body", ""),
        }
    if "client_error_type" in response:
        return {
            "response_type": "client_error",
            "error_type": response["client_error_type"],
            "error_message": response.get("client_error_message", ""),
        }
    message = response.get("message", response)
    if not isinstance(message, dict):
        return {"response_type": "invalid_message"}
    content = message.get("content")
    tool_calls = message.get("tool_calls") or []
    return {
        "model": response.get("model"),
        "message_keys": sorted(message),
        "content_is_empty": content in (None, ""),
        "content_preview": content[:1000] if isinstance(content, str) else None,
        "tool_call_count": len(tool_calls),
        "tool_arguments_type": (
            type(tool_calls[0].get("function", {}).get("arguments")).__name__
            if tool_calls else None
        ),
        "tool_arguments_preview": (
            str(tool_calls[0].get("function", {}).get("arguments"))[:1000]
            if tool_calls else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=len(CASES))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/knowledge_classification_real_model.jsonl"),
    )
    args = parser.parse_args()
    backend = build_knowledge_llm_backend()
    if backend is None:
        raise SystemExit("LLM 未启用或 API Key 未配置")
    backend = RecordingBackend(backend)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with Session(engine) as session:
        ensure_calculus_taxonomy(session)
        for label, body, solution, expected in CASES[: args.limit]:
            result = classify_text_with_llm(
                session,
                question_body=body,
                standard_solution=solution,
                solution_steps=[solution],
                backend=backend,
            )
            records.append({
                "case": label,
                "question_body": body,
                "rule_candidates": [item["name"] for item in result["candidate_knowledge_points"]],
                "llm_primary": (result["primary_knowledge_point"] or {}).get("name"),
                "llm_secondary": [item["name"] for item in result["secondary_knowledge_points"]],
                "confidence": result["confidence"],
                "needs_review": result["needs_review"],
                "provenance": result["provenance"],
                "fallback_reason": result["fallback_reason"],
                "llm_raw_response_type": result["llm_raw_response_type"],
                "schema_validation_errors": result["schema_validation_errors"],
                "llm_attempt_count": result["llm_attempt_count"],
                "human_expected_result": expected,
                "reason": result["reason"],
                "raw_response_diagnostic": _raw_response_diagnostic(backend.last_response),
            })
    output.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    counts = {
        "total": len(records),
        "llm_valid": sum(record["provenance"] == "llm_suggested" for record in records),
        "rule_fallback": sum(record["provenance"] == "rule_fallback" for record in records),
        "json_decode_error": sum(record["fallback_reason"] == "json_decode_error" for record in records),
        "validation_error": sum(record["fallback_reason"] == "schema_validation_error" for record in records),
        "api_error": sum(record["fallback_reason"] == "api_error" for record in records),
        "timeout": sum(record["fallback_reason"] == "llm_timeout" for record in records),
        "candidate_cases_fully_recalled": sum(
            set(record["human_expected_result"]).issubset(record["rule_candidates"])
            for record in records
        ),
        "candidate_expected_labels": sum(len(record["human_expected_result"]) for record in records),
        "candidate_recalled_labels": sum(
            len(set(record["human_expected_result"]) & set(record["rule_candidates"]))
            for record in records
        ),
        "output": str(output),
    }
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()

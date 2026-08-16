import json

import pytest

from calculus_agent.prep import vision
from calculus_agent.prep.vision import BailianVisionExtractor


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self) -> bytes:
        content = {
            "question_text": "已知 $y=-2x+5$，求斜率。",
            "options": [],
            "question_type": "计算题",
            "final_answer": "$-2$",
            "solution_text": "一次函数 $y=kx+b$ 的斜率为 $k$。",
            "knowledge_names": ["一次函数"],
            "warnings": [],
        }
        return json.dumps(
            {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]},
            ensure_ascii=False,
        ).encode()


def test_extracts_structured_question_from_bailian(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(vision, "urlopen", fake_urlopen)
    extractor = BailianVisionExtractor(
        api_key="test-key",
        base_url="https://example.com/compatible-mode/v1",
        model="qwen3-vl-plus",
        timeout=30,
    )
    result = extractor.extract("data:image/png;base64,YWJj")

    assert result.question_type == "计算题"
    assert result.final_answer == "$-2$"
    assert result.needs_review is True
    assert captured["url"].endswith("/chat/completions")
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "qwen3-vl-plus"
    assert captured["timeout"] == 30


def test_rejects_unsupported_image_type():
    extractor = BailianVisionExtractor(
        api_key="test-key", base_url="https://example.com", model="qwen3-vl-plus"
    )
    with pytest.raises(ValueError, match="仅支持"):
        extractor.extract("data:image/gif;base64,YWJj")

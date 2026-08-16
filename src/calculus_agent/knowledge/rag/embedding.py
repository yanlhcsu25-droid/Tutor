"""Embedding 层：把文本映射为向量。

设计要点：
- EmbeddingProvider 是抽象接口，Retriever 不依赖任何具体模型。
- 具体 Provider 由 get_embedding_provider() 按 settings 选择，密钥 / Base URL / Model 全部来自 .env。
- 提供两种实现：
    * LocalHashingEmbedding：离线、确定性、无需网络与密钥，用于测试与无 API 场景。
      采用中文字符 unigram/bigram + 英文词 的哈希向量（hashing trick），维度固定。
    * SiliconFlowEmbedding：OpenAI 兼容 /embeddings 端点，使用项目已有 SILICONFLOW_* 配置，
      获得真实语义向量（语义检索质量更高）。
- 第一版只需要文本 embedding。
"""

from __future__ import annotations

import abc
import hashlib
import json
import math
import re
import urllib.request
from collections.abc import Sequence

from calculus_agent.config import get_settings

_PUNCT = set("，。、；：？！“”‘’（）《》【】.,;:?!()[]{}<>/\\[]@#%^*+=|~`\"' ")


class EmbeddingProvider(abc.ABC):
    dim: int = 0

    @abc.abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

    @abc.abstractmethod
    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


class LocalHashingEmbedding(EmbeddingProvider):
    """离线确定性 embedding：哈希 trick，固定维度，无需网络。"""

    def __init__(self, dim: int = 2048) -> None:
        self.dim = dim

    @staticmethod
    def _tokens(text: str) -> list[str]:
        tokens: list[str] = []
        cleaned = re.sub(r"\s+", "", text)
        for i, ch in enumerate(cleaned):
            if ch in _PUNCT:
                continue
            tokens.append(ch)
            if i + 1 < len(cleaned):
                nxt = cleaned[i + 1]
                if nxt not in _PUNCT:
                    tokens.append(ch + nxt)
        for word in re.findall(r"[a-zA-Z0-9]+", text.lower()):
            tokens.append(word)
        return tokens

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in self._tokens(text):
            digest = hashlib.md5(tok.encode("utf-8")).hexdigest()
            idx = int(digest, 16) % self.dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class SiliconFlowEmbedding(EmbeddingProvider):
    """OpenAI 兼容的语义 embedding（SiliconFlow），复用项目已有 SILICONFLOW_* 配置。"""

    def __init__(self, api_key: str, base_url: str, model: str, batch_size: int = 32) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.batch_size = batch_size

    def _post(self, inputs: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": inputs}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = sorted(data["data"], key=lambda x: x["index"])
        vectors = [item["embedding"] for item in items]
        if not self.dim and vectors:
            self.dim = len(vectors[0])
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        texts = list(texts)
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            out.extend(self._post(texts[start : start + self.batch_size]))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def get_embedding_provider() -> EmbeddingProvider:
    """按 settings 选择 embedding provider；密钥 / URL / Model 全部来自 .env，不硬编码。"""
    settings = get_settings()
    provider = (settings.textbook_embedding_provider or "local").lower()
    if provider == "siliconflow":
        if not settings.siliconflow_api_key:
            raise RuntimeError(
                "已选择 siliconflow embedding，但未配置 SILICONFLOW_API_KEY，请检查 .env"
            )
        return SiliconFlowEmbedding(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            model=settings.textbook_embedding_model,
        )
    if provider in {"local", ""}:
        return LocalHashingEmbedding(dim=settings.textbook_embedding_dim)
    raise ValueError(f"未知的 textbook_embedding_provider：{provider}")

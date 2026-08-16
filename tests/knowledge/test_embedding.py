"""Embedding 接口测试：维度一致、归一化、provider 工厂。"""

from __future__ import annotations

import math

from calculus_agent.knowledge.rag.embedding import (
    EmbeddingProvider,
    LocalHashingEmbedding,
    get_embedding_provider,
)


def test_local_provider_dim_consistency():
    p = LocalHashingEmbedding(dim=1024)
    docs = p.embed_documents(["函数的凹凸性", "求极限"])
    q = p.embed_query("拐点与凹凸区间")
    assert p.dim == 1024
    assert all(len(v) == 1024 for v in docs)
    assert len(q) == 1024


def test_local_provider_normalized():
    p = LocalHashingEmbedding(dim=512)
    v = p.embed_query("二阶导数判定法")
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6


def test_local_provider_deterministic():
    p1 = LocalHashingEmbedding(dim=512)
    p2 = LocalHashingEmbedding(dim=512)
    assert p1.embed_query("极限运算法则") == p2.embed_query("极限运算法则")


def test_default_provider_is_local():
    provider = get_embedding_provider()
    assert isinstance(provider, EmbeddingProvider)
    assert isinstance(provider, LocalHashingEmbedding)


def test_local_provider_self_similarity_and_distinctness():
    # 说明：LocalHashingEmbedding 是离线词法向量，对极短文本的主题区分能力有限，
    # 这里只验证「相同文本相似度≈1、不同文本相似度<1」这一确定性契约。
    p = LocalHashingEmbedding(dim=2048)
    a = p.embed_query("函数的凹凸性")
    b = p.embed_query("函数的凹凸性")
    c = p.embed_query("求不定积分的基本公式")
    assert _cosine(a, b) == 1.0
    assert _cosine(a, c) < 1.0
    assert _cosine(a, c) > 0.0


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))

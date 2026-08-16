"""VectorStore 层基础测试：写入/搜索/clear/metadata。"""

from __future__ import annotations

from calculus_agent.knowledge.rag.schemas import KnowledgeChunk
from calculus_agent.knowledge.rag.vector_store import SqliteVectorStore


def _make_chunks():
    return [
        KnowledgeChunk(
            id="t::0", text="函数的凹凸性定义与判定", source_file="book.md",
            chapter="第三章", section="第三节", heading="函数的凹凸性", chunk_index=0,
        ),
        KnowledgeChunk(
            id="t::1", text="函数极限的定义与运算法则", source_file="book.md",
            chapter="第一章", section="第二节", heading="函数极限", chunk_index=1,
        ),
    ]


def _store_with_data():
    store = SqliteVectorStore(":memory:")
    chunks = _make_chunks()
    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    store.add_chunks(list(zip(chunks, vectors)))
    return store, chunks


def test_add_and_count():
    store, _ = _store_with_data()
    assert store.count() == 2


def test_search_returns_top_k_and_metadata():
    store, _ = _store_with_data()
    # 查询向量贴近第一个 chunk
    results = store.search([0.9, 0.1, 0.0], top_k=5)
    assert len(results) == 2  # 不足 top_k 时返回全部
    assert results[0]["chunk_id"] == "t::0"
    assert results[0]["heading"] == "函数的凹凸性"
    assert results[0]["chapter"] == "第三章"
    assert "函数的凹凸性" in results[0]["text"]
    assert results[0]["source_file"] == "book.md"


def test_search_respects_top_k():
    store, _ = _store_with_data()
    results = store.search([0.0, 1.0, 0.0], top_k=1)
    assert len(results) == 1
    assert results[0]["chunk_id"] == "t::1"


def test_clear():
    store, _ = _store_with_data()
    store.clear()
    assert store.count() == 0
    assert store.search([1.0, 0.0, 0.0], top_k=5) == []

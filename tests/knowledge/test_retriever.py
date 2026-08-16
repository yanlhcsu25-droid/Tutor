"""Retriever 集成测试：建库 + 检索，验证顶层契约。"""

from __future__ import annotations

from pathlib import Path

from calculus_agent.knowledge.rag.embedding import LocalHashingEmbedding
from calculus_agent.knowledge.rag.parser import MarkdownTextbookParser
from calculus_agent.knowledge.rag.retriever import KnowledgeRetriever
from calculus_agent.knowledge.rag.vector_store import SqliteVectorStore

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "calculus_textbook_sample.md"


def _retriever():
    embedding = LocalHashingEmbedding(dim=2048)
    store = SqliteVectorStore(":memory:")
    return KnowledgeRetriever(embedding_provider=embedding, vector_store=store)


def test_index_and_retrieve():
    retriever = _retriever()
    doc = MarkdownTextbookParser().parse(FIXTURE)
    n = retriever.index_document(doc)
    assert n > 0

    results = retriever.retrieve_knowledge("求函数的凹凸区间并判断拐点", top_k=5)
    assert len(results) <= 5
    assert results, "应返回检索结果"
    for r in results:
        assert r.text and r.source_file
        assert hasattr(r, "score")
        assert r.chunk_id


def test_source_metadata_not_lost():
    retriever = _retriever()
    retriever.index_markdown(
        "# 测试章\n\n本节讨论一阶导数的计算方法，包括基本求导公式与复合函数求导。",
        source_file="demo.md",
    )
    results = retriever.retrieve_knowledge("一阶导数", top_k=3)
    assert results
    assert all(r.source_file == "demo.md" for r in results)


def test_clear_empties_index():
    retriever = _retriever()
    retriever.index_markdown("# 章\n\n内容", source_file="x.md")
    retriever.clear()
    assert retriever.retrieve_knowledge("内容", top_k=5) == []

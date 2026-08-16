"""KnowledgeRetriever：对外统一入口。

上层业务只调用：
    retrieve_knowledge(query, top_k=5) -> list[RetrievedKnowledge]

不需要知道底层用的是 Local / SiliconFlow embedding，或是 SQLite / 未来的混合检索。
后续升级路径（Vector -> Vector+BM25 -> Reranker）都可以在本层或其组合层内部完成，
不改动调用方。

同时提供建库能力：index_document / index_markdown / clear。
"""

from __future__ import annotations

from pathlib import Path

from calculus_agent.knowledge.rag.chunker import StructureAwareChunker
from calculus_agent.knowledge.rag.embedding import EmbeddingProvider
from calculus_agent.knowledge.rag.schemas import ParsedDocument, RetrievedKnowledge
from calculus_agent.knowledge.rag.vector_store import VectorStore


class KnowledgeRetriever:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        chunker: StructureAwareChunker | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.chunker = chunker or StructureAwareChunker()

    @staticmethod
    def _embed_text(chunk) -> str:
        """用于向量化的文本：把层级标题作为上下文前置，提升检索命中率。

        注意：这仅用于 embedding，不影响返回给上层的 chunk.text（正文原文）。
        """
        context = " / ".join(p for p in (chunk.chapter, chunk.section, chunk.heading) if p)
        if context:
            return f"{context}\n{chunk.text}"
        return chunk.text

    def index_document(self, doc: ParsedDocument) -> int:
        chunks = self.chunker.chunk(doc)
        if not chunks:
            return 0
        vectors = self.embedding_provider.embed_documents([self._embed_text(c) for c in chunks])
        self.vector_store.add_chunks(list(zip(chunks, vectors)))
        return len(chunks)

    def index_markdown(self, markdown: str, source_file: str = "textbook.md") -> int:
        return self.index_document(ParsedDocument.from_markdown(markdown, source_file=source_file))

    def retrieve_knowledge(self, query: str, top_k: int = 5) -> list[RetrievedKnowledge]:
        q_vec = self.embedding_provider.embed_query(query)
        rows = self.vector_store.search(q_vec, top_k=top_k)
        return [
            RetrievedKnowledge(
                text=r["text"],
                score=r["score"],
                chapter=r["chapter"],
                section=r["section"],
                heading=r["heading"],
                source_file=r["source_file"],
                chunk_id=r["chunk_id"],
            )
            for r in rows
        ]

    def clear(self) -> None:
        self.vector_store.clear()

"""教材知识库 V1 独立检索链路（RAG）。

五层解耦：
    DocumentParser -> Chunker -> EmbeddingProvider -> VectorStore -> KnowledgeRetriever

本模块只负责「教材知识检索」本身，不接入 Agent 主循环，不修改现有知识点分类逻辑。
"""

from calculus_agent.knowledge.rag.chunker import StructureAwareChunker
from calculus_agent.knowledge.rag.embedding import (
    EmbeddingProvider,
    LocalHashingEmbedding,
    SiliconFlowEmbedding,
    get_embedding_provider,
)
from calculus_agent.knowledge.rag.parser import (
    DocumentParser,
    MarkdownTextbookParser,
    MinerUTextbookParser,
)
from calculus_agent.knowledge.rag.retriever import KnowledgeRetriever
from calculus_agent.knowledge.rag.schemas import (
    KnowledgeChunk,
    ParsedDocument,
    RetrievedKnowledge,
)
from calculus_agent.knowledge.rag.vector_store import SqliteVectorStore, VectorStore

__all__ = [
    "StructureAwareChunker",
    "EmbeddingProvider",
    "LocalHashingEmbedding",
    "SiliconFlowEmbedding",
    "get_embedding_provider",
    "DocumentParser",
    "MarkdownTextbookParser",
    "MinerUTextbookParser",
    "KnowledgeRetriever",
    "KnowledgeChunk",
    "ParsedDocument",
    "RetrievedKnowledge",
    "SqliteVectorStore",
    "VectorStore",
]

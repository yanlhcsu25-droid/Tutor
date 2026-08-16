"""VectorStore 层：最小可用向量存储。

选择：SQLite（项目已有技术栈，零外部服务，低侵入）。
- 不引入 GraphRAG / LightRAG / Elasticsearch / Redis / 独立向量数据库服务。
- 支持 add_chunks / search / clear，并完整保存 metadata：
    chapter / section / heading / source_file / chunk_id
- 相似度用余弦（numpy 可用时走 numpy，否则回退纯 Python）。
- 后续要升级到 BM25 / Reranker / 混合检索，只需替换本层或新增组合层，
  上层 KnowledgeRetriever 的调用方式不变。
"""

from __future__ import annotations

import abc
import json
import math
import sqlite3
from pathlib import Path

from calculus_agent.knowledge.rag.schemas import KnowledgeChunk

try:
    import numpy as np

    _HAS_NUMPY = True
except Exception:  # pragma: no cover - numpy 缺失时回退
    _HAS_NUMPY = False
    np = None


class VectorStore(abc.ABC):
    @abc.abstractmethod
    def add_chunks(self, items: list[tuple[KnowledgeChunk, list[float]]]) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def search(self, query_embedding: list[float], top_k: int = 5) -> list[dict]:
        raise NotImplementedError

    @abc.abstractmethod
    def clear(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def count(self) -> int:
        raise NotImplementedError


class SqliteVectorStore(VectorStore):
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_chunks (
                id TEXT PRIMARY KEY,
                embedding TEXT NOT NULL,
                chapter TEXT,
                section TEXT,
                heading TEXT,
                source_file TEXT,
                chunk_index INTEGER,
                text TEXT
            )
            """
        )
        self._conn.commit()

    def add_chunks(self, items: list[tuple[KnowledgeChunk, list[float]]]) -> None:
        for chunk, vector in items:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO rag_chunks
                (id, embedding, chapter, section, heading, source_file, chunk_index, text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.id,
                    json.dumps(vector),
                    chunk.chapter,
                    chunk.section,
                    chunk.heading,
                    chunk.source_file,
                    chunk.chunk_index,
                    chunk.text,
                ),
            )
        self._conn.commit()

    def search(self, query_embedding: list[float], top_k: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, embedding, chapter, section, heading, source_file, text FROM rag_chunks"
        ).fetchall()
        if not rows:
            return []

        if _HAS_NUMPY:
            q = np.asarray(query_embedding, dtype=float)
            q_norm = float(np.linalg.norm(q)) or 1.0
            scored = []
            for row in rows:
                vec = np.asarray(json.loads(row[1]), dtype=float)
                v_norm = float(np.linalg.norm(vec)) or 1.0
                sim = float(np.dot(q, vec) / (q_norm * v_norm))
                scored.append((sim, row))
        else:  # pragma: no cover - 回退路径
            q = query_embedding
            q_norm = math.sqrt(sum(x * x for x in q)) or 1.0
            scored = []
            for row in rows:
                vec = json.loads(row[1])
                dot = sum(a * b for a, b in zip(q, vec))
                v_norm = math.sqrt(sum(x * x for x in vec)) or 1.0
                scored.append((dot / (q_norm * v_norm), row))

        scored.sort(key=lambda x: -x[0])
        results = []
        for sim, row in scored[:top_k]:
            results.append(
                {
                    "chunk_id": row[0],
                    "score": round(sim, 6),
                    "chapter": row[2],
                    "section": row[3],
                    "heading": row[4],
                    "source_file": row[5],
                    "text": row[6],
                }
            )
        return results

    def clear(self) -> None:
        self._conn.execute("DELETE FROM rag_chunks")
        self._conn.commit()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]

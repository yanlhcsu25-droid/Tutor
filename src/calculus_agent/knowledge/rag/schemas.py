"""RAG 链路的数据结构定义。

所有跨层传递的对象都在这里集中声明，保证 Parser / Chunker / Embedding /
VectorStore / Retriever 之间的契约稳定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ParsedDocument:
    """DocumentParser 的统一输出，与具体解析器（MinerU 等）解耦。"""

    markdown: str
    source_file: str

    @classmethod
    def from_markdown(cls, markdown: str, source_file: str = "textbook.md") -> "ParsedDocument":
        return cls(markdown=markdown, source_file=source_file)


@dataclass
class KnowledgeChunk:
    """Structure-aware Chunker 的输出。

    chapter / section / heading 会从 Markdown 标题层级继承到正文 chunk，
    即便正文被递归切分，子块也保留父级标题信息。
    """

    id: str
    text: str
    source_file: str
    chapter: str = ""
    section: str = ""
    heading: str = ""
    chunk_index: int = 0


@dataclass
class RetrievedKnowledge:
    """KnowledgeRetriever 返回给上层业务的检索结果。

    上层（如未来的知识点分类）只看到这些字段，不需要知道底层用了哪个向量库。
    """

    text: str
    score: float
    chapter: str
    section: str
    heading: str
    source_file: str
    chunk_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "score": self.score,
            "chapter": self.chapter,
            "section": self.section,
            "heading": self.heading,
            "source_file": self.source_file,
            "chunk_id": self.chunk_id,
        }

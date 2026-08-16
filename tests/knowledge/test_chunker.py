"""Chunker 层基础测试：结构感知、标题继承、metadata 完整性。"""

from __future__ import annotations

from pathlib import Path

from calculus_agent.knowledge.rag.chunker import StructureAwareChunker
from calculus_agent.knowledge.rag.parser import MarkdownTextbookParser

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "calculus_textbook_sample.md"


def _chunks():
    doc = MarkdownTextbookParser().parse(FIXTURE)
    return StructureAwareChunker().chunk(doc)


def test_chunks_non_empty():
    chunks = _chunks()
    assert chunks, "应当至少切出一个 chunk"
    assert all(c.text.strip() for c in chunks), "chunk 文本不应为空"


def test_metadata_present():
    chunks = _chunks()
    for c in chunks:
        assert c.id and c.source_file and c.text
        assert isinstance(c.chunk_index, int)


def test_heading_inheritance():
    chunks = _chunks()
    # 找到「函数的凹凸性」小节下的 chunk，应继承 chapter=第三章、section=第三节
    target = [c for c in chunks if "函数的凹凸性" in (c.heading or c.text)][:1]
    assert target, "应当存在函数的凹凸性相关 chunk"
    c = target[0]
    assert "第三章" in c.chapter, f"chapter 未继承: {c.chapter!r}"
    assert "第三节" in c.section, f"section 未继承: {c.section!r}"
    assert "凹凸" in (c.heading or ""), f"heading 未捕获: {c.heading!r}"


def test_chunk_index_unique_and_ordered():
    chunks = _chunks()
    indices = [c.chunk_index for c in chunks]
    assert indices == sorted(indices)
    assert len(set(c.id for c in chunks)) == len(chunks)

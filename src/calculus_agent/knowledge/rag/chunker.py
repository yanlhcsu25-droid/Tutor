"""Structure-aware Chunker 层：Markdown -> 带层级 metadata 的知识块。

切分策略（不使用 LLM，纯 heuristic）：
1. 优先利用 Markdown 标题层级（# 章 / ## 节 / ### 小节 / #### 子目）。
2. 兼容教材中不以 # 开头、但形如「第X章」「第X节」的明显章节标题。
3. 标题信息（chapter / section / heading）从父级继承到每个正文 chunk，
   即使正文被递归切分，子块也保留父级标题。
4. 单个标题下的正文过长时，按段落递归切分，并保留少量 overlap。
5. 第一版不做 Parent-Child Chunk。

每个 KnowledgeChunk 至少保存：
    id / text / source_file / chapter / section / heading / chunk_index
"""

from __future__ import annotations

import re

from calculus_agent.knowledge.rag.schemas import KnowledgeChunk, ParsedDocument

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_CHAPTER_RE = re.compile(r"^第\s*[一二三四五六七八九十百0-9]+\s*章\b")
_SECTION_RE = re.compile(r"^第\s*[一二三四五六七八九十百0-9]+\s*节\b")
_SPLIT_RE = re.compile(r"\n\s*\n")


class StructureAwareChunker:
    def __init__(
        self,
        max_chars: int = 1500,
        overlap_chars: int = 200,
        min_chars: int = 30,
    ) -> None:
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars
        self.min_chars = min_chars

    def chunk(self, doc: ParsedDocument) -> list[KnowledgeChunk]:
        chapter = section = heading = ""
        buffer: list[str] = []
        chunks: list[KnowledgeChunk] = []
        index = 0

        def flush() -> None:
            nonlocal buffer, index
            text = "\n".join(buffer).strip()
            buffer = []
            if len(text) < self.min_chars:
                # 太短的内容（如孤立的空行残留）直接丢弃，不单独成块
                if text:
                    # 仍不足 min_chars，但非空，合并进下一个 buffer 不合适，这里直接追加到上一块由调用方处理
                    pass
                return
            for part in self._split_long(text):
                chunks.append(
                    KnowledgeChunk(
                        id=f"{doc.source_file}::{index}",
                        text=part,
                        source_file=doc.source_file,
                        chapter=chapter,
                        section=section,
                        heading=heading,
                        chunk_index=index,
                    )
                )
                index += 1

        for raw_line in doc.markdown.splitlines():
            line = raw_line.strip()
            if not line:
                buffer.append(raw_line)
                continue

            heading_match = _HEADING_RE.match(line)
            if heading_match:
                flush()
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                if level == 1:
                    chapter, section, heading = title, "", ""
                elif level == 2:
                    section, heading = title, ""
                else:
                    heading = title
                continue

            if _CHAPTER_RE.match(line):
                flush()
                chapter, section, heading = line, "", ""
                continue
            if _SECTION_RE.match(line):
                flush()
                section, heading = line, ""
                continue

            buffer.append(raw_line)

        flush()
        return chunks

    def _split_long(self, text: str) -> list[str]:
        """对超过 max_chars 的正文按段落递归切分，并保留 overlap。"""
        if len(text) <= self.max_chars:
            return [text]

        paragraphs = [p for p in _SPLIT_RE.split(text) if p.strip()]
        if not paragraphs:
            paragraphs = [text]

        merged: list[str] = []
        current = ""
        for para in paragraphs:
            if current and len(current) + len(para) + 2 > self.max_chars:
                merged.append(current)
                current = para
            else:
                current = (current + "\n\n" + para) if current else para
        if current:
            merged.append(current)

        # 相邻块保留 overlap，避免定义/定理被从中间硬切断后语义断裂
        result: list[str] = []
        for i, piece in enumerate(merged):
            if i > 0 and self.overlap_chars > 0:
                prev_tail = merged[i - 1][-self.overlap_chars :]
                piece = prev_tail + "\n" + piece
            result.append(piece)
        return result

"""DocumentParser 层：教材 PDF -> 统一 Markdown。

设计原则：
- Parser 与后续 RAG（Chunker/Retriever）完全解耦：只产出 ParsedDocument(markdown, source_file)。
- 复用项目统一的 MinerU 能力（src/calculus_agent/ocr/mineru_adapter.py），
  不重复实现 OCR，也不接视觉大模型。
- 同时提供 MarkdownTextbookParser，便于直接摄入已解析好的教材 Markdown（测试 / 离线场景），
  以及未来替换解析器时无需改动 Chunker / Retriever。
"""

from __future__ import annotations

import abc
from pathlib import Path

from calculus_agent.knowledge.rag.schemas import ParsedDocument


class DocumentParser(abc.ABC):
    """解析器抽象。新增解析器只需实现 parse()。"""

    @abc.abstractmethod
    def parse(self, source: Path) -> ParsedDocument:
        """将 source（PDF 或 Markdown 等）解析为统一 ParsedDocument。"""
        raise NotImplementedError


class MinerUTextbookParser(DocumentParser):
    """基于项目已有 MinerU 适配器的 PDF 解析器。

    依赖 .venv-mineru 中的 mineru 二进制（已由现有 OCR 链路验证）。
    输出按原始页码聚合的 Markdown，章节标题以 Markdown heading 形式保留。
    """

    def parse(self, source: Path) -> ParsedDocument:
        from calculus_agent.ocr.mineru_adapter import (
            MinerUError,
            content_blocks_to_pages,
            run_mineru,
        )
        import pypdfium2 as pdfium

        source = Path(source).resolve()
        if not source.is_file():
            raise MinerUError(f"找不到教材 PDF：{source}")

        output_dir = source.parent / f".mineru_{source.stem}"
        try:
            blocks, _metrics = run_mineru(source, output_dir)
        except MinerUError:
            raise

        with pdfium.PdfDocument(str(source)) as doc:
            page_numbers = tuple(range(1, len(doc) + 1))
        pages = content_blocks_to_pages(blocks, page_numbers)
        markdown = "\n\n".join(md for _, md in pages).strip()
        return ParsedDocument(markdown=markdown, source_file=source.name)


class MarkdownTextbookParser(DocumentParser):
    """直接摄入已解析好的 Markdown 教材文件（不经过 MinerU）。

    适用于：已有 MinerU 产物、人工整理教材、或测试 fixture。
    """

    def parse(self, source: Path) -> ParsedDocument:
        source = Path(source).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"找不到教材 Markdown：{source}")
        markdown = source.read_text(encoding="utf-8")
        return ParsedDocument(markdown=markdown, source_file=source.name)

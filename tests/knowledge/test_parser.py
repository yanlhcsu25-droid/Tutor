"""Parser 层基础测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from calculus_agent.knowledge.rag.parser import (
    DocumentParser,
    MarkdownTextbookParser,
    MinerUTextbookParser,
)
from calculus_agent.ocr.mineru_adapter import MinerUError

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "calculus_textbook_sample.md"


def test_markdown_parser_returns_parsed_document():
    parser = MarkdownTextbookParser()
    doc = parser.parse(FIXTURE)
    assert isinstance(doc, object)
    assert "函数的凹凸性" in doc.markdown
    assert doc.source_file == FIXTURE.name


def test_markdown_parser_missing_file_raises():
    parser = MarkdownTextbookParser()
    with pytest.raises(FileNotFoundError):
        parser.parse(Path("/no/such/textbook.md"))


def test_mineru_parser_is_document_parser():
    assert issubclass(MinerUTextbookParser, DocumentParser)


def test_mineru_parser_missing_pdf_raises():
    parser = MinerUTextbookParser()
    with pytest.raises(MinerUError):
        parser.parse(Path("/no/such/textbook.pdf"))

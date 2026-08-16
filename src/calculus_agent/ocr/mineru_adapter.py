"""Isolated MinerU CLI adapter for page-indexed Markdown output."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium


class MinerUError(RuntimeError):
    pass


class MinerUCancelled(MinerUError):
    pass


def resolve_mineru_binary() -> Path:
    configured = os.getenv("CALCULUS_AGENT_MINERU_BIN")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path(__file__).resolve().parents[3] / ".venv-mineru" / "bin" / "mineru",
        Path(shutil.which("mineru")) if shutil.which("mineru") else None,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise MinerUError(
        "找不到 MinerU。请安装项目的 .venv-mineru，或设置 CALCULUS_AGENT_MINERU_BIN"
    )


def prepare_selected_pdf(
    source_path: Path,
    output_path: Path,
    selected_pages: Sequence[int] | None,
) -> tuple[Path, tuple[int, ...]]:
    """Create a lossless selected-page PDF and return original one-based page numbers."""
    source = pdfium.PdfDocument(str(source_path))
    try:
        available = tuple(range(1, len(source) + 1))
        pages = tuple(selected_pages) if selected_pages else available
        if not pages:
            raise MinerUError("PDF 没有可识别页面")
        invalid = set(pages) - set(available)
        if invalid:
            raise MinerUError(f"页码超出 PDF 范围：{sorted(invalid)}")
        if len(pages) != len(set(pages)):
            raise MinerUError("PDF 页码不能重复")
        if pages == available:
            return source_path, pages

        output_path.parent.mkdir(parents=True, exist_ok=True)
        selected = pdfium.PdfDocument.new()
        try:
            selected.import_pages(source, pages=[page - 1 for page in pages])
            selected.save(str(output_path))
        finally:
            selected.close()
        return output_path, pages
    finally:
        source.close()


def run_mineru(
    pdf_path: Path,
    output_dir: Path,
    *,
    cancel_callback: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    timeout_seconds: float = 3600.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run MinerU in its dedicated environment and return flat content blocks."""
    binary = resolve_mineru_binary()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "mineru.log"
    command = [
        str(binary),
        "-p", str(pdf_path),
        "-o", str(output_dir),
        "-b", "hybrid-engine",
        "--effort", "medium",
        "-m", "auto",
        "-f", "true",
        "-t", "false",
        "--image-analysis", "false",
    ]
    started = time.monotonic()
    peak_rss_mb = 0.0
    log_position = 0
    log_tail = ""
    reported_progress: tuple[str, int] | None = None
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        try:
            while process.poll() is None:
                if cancel_callback and cancel_callback():
                    _terminate_process_tree(process)
                    raise MinerUCancelled("用户已停止 MinerU OCR")
                elapsed = time.monotonic() - started
                if elapsed > timeout_seconds:
                    _terminate_process_tree(process)
                    raise MinerUError(f"MinerU OCR 超时（>{timeout_seconds:.0f}s）")
                peak_rss_mb = max(peak_rss_mb, _process_tree_rss_mb(process.pid))
                log_position, log_tail, progress = _read_mineru_progress(
                    log_path, log_position, log_tail
                )
                if progress is not None and progress_callback is not None:
                    current, total, stage = progress
                    percent = int(current * 100 / total) if total else 0
                    marker = (stage, percent)
                    if marker != reported_progress:
                        progress_callback(current, total, stage)
                        reported_progress = marker
                time.sleep(0.25)
        finally:
            if process.poll() is None:
                _terminate_process_tree(process)

    elapsed = time.monotonic() - started
    if process.returncode != 0:
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-30:])
        raise MinerUError(f"MinerU 运行失败（退出码 {process.returncode}）：\n{tail}")
    candidates = list(output_dir.rglob("*_content_list.json"))
    candidates = [path for path in candidates if not path.name.endswith("_content_list_v2.json")]
    if len(candidates) != 1:
        raise MinerUError(f"MinerU 没有生成唯一的 content_list.json：{candidates}")
    blocks = json.loads(candidates[0].read_text(encoding="utf-8"))
    if not isinstance(blocks, list):
        raise MinerUError("MinerU content_list.json 格式无效")
    metrics = {
        "binary": str(binary),
        "command": command,
        "elapsed_seconds": elapsed,
        "peak_rss_mb": peak_rss_mb,
        "content_list": str(candidates[0]),
        "log": str(log_path),
    }
    return blocks, metrics


_PROGRESS_PATTERN = re.compile(
    r"(?:^|[\r\n])\s*(Layout Predict|Predict|OCR-det|Processing pages):"
    r"\s*\d+%\|[^\r\n]*?\|\s*(\d+)/(\d+)"
)
_PROGRESS_STAGES = {
    "Layout Predict": "mineru_layout",
    "Predict": "mineru_predict",
    "OCR-det": "mineru_ocr",
    "Processing pages": "mineru_pages",
}


def _read_mineru_progress(
    log_path: Path, position: int, previous_tail: str
) -> tuple[int, str, tuple[int, int, str] | None]:
    """Read newly appended tqdm output and return its latest progress event."""
    try:
        with log_path.open("rb") as stream:
            stream.seek(position)
            chunk = stream.read()
            next_position = stream.tell()
    except OSError:
        return position, previous_tail, None
    if not chunk:
        return position, previous_tail, None
    text = previous_tail + chunk.decode("utf-8", errors="replace")
    matches = list(_PROGRESS_PATTERN.finditer(text))
    progress = None
    if matches:
        match = matches[-1]
        current = int(match.group(2))
        total = int(match.group(3))
        if total > 0:
            progress = (min(current, total), total, _PROGRESS_STAGES[match.group(1)])
    return next_position, text[-512:], progress


def content_blocks_to_pages(
    blocks: Sequence[dict[str, Any]],
    original_page_numbers: Sequence[int],
) -> list[tuple[int, str]]:
    """Convert MinerU's zero-based page blocks into page-scoped Markdown."""
    grouped: dict[int, list[str]] = {index: [] for index in range(len(original_page_numbers))}
    for block in blocks:
        try:
            page_index = int(block.get("page_idx", -1))
        except (TypeError, ValueError):
            continue
        if page_index not in grouped:
            continue
        markdown = _block_markdown(block)
        if markdown:
            grouped[page_index].append(markdown)
    pages: list[tuple[int, str]] = []
    for page_index, original_page in enumerate(original_page_numbers):
        markdown = "\n\n".join(grouped[page_index]).strip()
        pages.append((original_page, markdown + ("\n" if markdown else "")))
    return pages


def _block_markdown(block: dict[str, Any]) -> str:
    block_type = str(block.get("type", ""))
    if block_type in {"header", "footer", "page_number", "discarded"}:
        return ""
    text = str(block.get("text") or "").strip()
    if block_type == "text":
        level = block.get("text_level")
        if isinstance(level, int) and level > 0 and text:
            return f"{'#' * min(level, 6)} {text}"
        return text
    if block_type == "equation":
        return text
    if block_type == "table":
        return str(block.get("table_body") or text).strip()
    if block_type == "image":
        captions = block.get("image_caption") or []
        caption = " ".join(str(item).strip() for item in captions if str(item).strip())
        return "[图片内容暂未解析，请人工核对原PDF]" + (f"\n\n{caption}" if caption else "")
    return text


def _process_tree_rss_mb(pid: int) -> float:
    try:
        import psutil

        process = psutil.Process(pid)
        processes = [process, *process.children(recursive=True)]
        return sum(item.memory_info().rss for item in processes if item.is_running()) / 1024 / 1024
    except Exception:
        return 0.0


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    try:
        import psutil

        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for item in children:
            item.terminate()
        parent.terminate()
        _, alive = psutil.wait_procs([*children, parent], timeout=5)
        for item in alive:
            item.kill()
    except Exception:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

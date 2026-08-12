#!/usr/bin/env python3
"""Isolated, single-page PPStructureV3 pixel-limit experiment worker.

This module is intentionally not wired into the production OCR route.  It
renders one PDF page under both a longest-edge and total-pixel ceiling, runs
PPStructureV3, saves Markdown, reports resource usage, and exits.
"""

from __future__ import annotations

import json
import math
import os
import resource
import sys
from pathlib import Path
from time import perf_counter

import pypdfium2 as pdfium
from PIL import Image


def adaptive_scale(
    width: float,
    height: float,
    *,
    preferred_scale: float,
    max_long_edge: int,
    max_pixels: int,
) -> float:
    """Return a non-enlarging scale satisfying both pixel ceilings."""
    edge_scale = max_long_edge / max(width, height)
    pixel_scale = math.sqrt(max_pixels / (width * height))
    return min(preferred_scale, edge_scale, pixel_scale)


def peak_rss_mb() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 * 1024 if sys.platform == "darwin" else 1024)


def main() -> int:
    if len(sys.argv) != 7:
        print(json.dumps({
            "status": "failed",
            "error": (
                "usage: ppstructure_worker.py PDF PAGE OUTPUT_MD "
                "MAX_LONG_EDGE MAX_PIXELS PREFERRED_SCALE"
            ),
        }))
        return 2

    pdf_path = Path(sys.argv[1])
    page_number = int(sys.argv[2])
    output_md = Path(sys.argv[3])
    max_long_edge = int(sys.argv[4])
    max_pixels = int(sys.argv[5])
    preferred_scale = float(sys.argv[6])
    started = perf_counter()

    try:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        document = pdfium.PdfDocument(str(pdf_path))
        try:
            page = document[page_number - 1]
            width, height = page.get_size()
            scale = adaptive_scale(
                width,
                height,
                preferred_scale=preferred_scale,
                max_long_edge=max_long_edge,
                max_pixels=max_pixels,
            )
            bitmap = page.render(scale=scale)
            image_path = output_md.with_suffix(".png")
            bitmap.to_pil().save(image_path)
            del bitmap, page
        finally:
            document.close()

        with Image.open(image_path) as image:
            input_width, input_height = image.size

        from paddleocr import PPStructureV3

        pipeline = PPStructureV3(device="cpu")
        result = next(iter(pipeline.predict(input=str(image_path))))
        output_md.parent.mkdir(parents=True, exist_ok=True)
        result.save_to_markdown(output_md)
        print(json.dumps({
            "status": "succeeded",
            "input_width": input_width,
            "input_height": input_height,
            "input_pixels": input_width * input_height,
            "render_scale": scale,
            "elapsed_seconds": round(perf_counter() - started, 3),
            "peak_rss_mb": round(peak_rss_mb(), 1),
            "markdown_path": str(output_md),
            "markdown_chars": len(output_md.read_text(encoding="utf-8")),
        }, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
            "elapsed_seconds": round(perf_counter() - started, 3),
            "peak_rss_mb": round(peak_rss_mb(), 1),
        }, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

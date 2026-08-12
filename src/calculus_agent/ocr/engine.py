"""PaddleOCR 引擎适配器 — 用于单张图片/单页 PDF 的文字识别。

从 Teacher Agent 的 paddle_ocr.py 精简移植：去掉多页文档管线，
只保留单图文字识别 + 阅读顺序排序。

注意事项：
- 在 FastAPI/uvicorn 环境中，OCR 通过 ThreadPoolExecutor 隔离执行，
  避免 PaddleOCR 内部 fork/multiprocess 与 uvicorn worker 冲突。
"""

import concurrent.futures
import os
from functools import lru_cache
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from time import perf_counter
from typing import Any

# macOS 上 PaddleOCR 内部 multiprocessing 需用 spawn 模式
if os.uname().sysname == "Darwin":
    import multiprocessing
    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

_OCR_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1)

from PIL import Image


class PaddleOcrUnavailable(RuntimeError):
    pass


class PaddleOcrEngine:
    """PaddleOCR 文字识别引擎（懒加载）。"""

    name = "paddleocr"

    def __init__(self, language: str = "ch") -> None:
        self.language = language

    @property
    def package_version(self) -> str | None:
        try:
            return version("paddleocr")
        except PackageNotFoundError:
            return None

    def recognize(
        self, image_path: str
    ) -> dict[str, Any]:
        """对单张图片运行 OCR（线程隔离执行），返回标准结果字典。"""
        if self.package_version is None:
            raise PaddleOcrUnavailable("PaddleOCR 未安装。请执行 pip install paddleocr。")

        try:
            started_at = perf_counter()
            future = _OCR_EXECUTOR.submit(_run_predict, self.language, image_path)
            blocks = future.result(timeout=300)
        except concurrent.futures.TimeoutError:
            return {
                "status": "failed",
                "engine": self.name,
                "engine_version": self.package_version,
                "warnings": ["OCR 识别超时（300s），请重试。"],
                "blocks": [],
            }
        except Exception as error:
            return {
                "status": "failed",
                "engine": self.name,
                "engine_version": self.package_version,
                "warnings": [f"PaddleOCR 识别失败: {type(error).__name__}: {error}"],
                "blocks": [],
            }

        return {
            "status": "succeeded",
            "engine": self.name,
            "engine_version": self.package_version,
            "duration_ms": round((perf_counter() - started_at) * 1000),
            "blocks": blocks,
            "warnings": [],
        }


def _run_predict(language: str, image_path: str) -> list[dict[str, Any]]:
    """在独立线程中执行 PaddleOCR 预测，避免与 uvicorn 事件循环冲突。"""
    pipeline = _build_pipeline(language)
    predictions = list(pipeline.predict(str(image_path)))
    blocks = _convert_predictions(predictions)
    return _reading_order(blocks)


@lru_cache(maxsize=4)
def _build_pipeline(language: str) -> Any:
    module = import_module("paddleocr")
    paddle_ocr_class = getattr(module, "PaddleOCR")
    return paddle_ocr_class(
        lang=language,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def _convert_predictions(predictions: list[Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for prediction in predictions:
        payload = prediction.json
        result = payload.get("res", payload)
        texts = result.get("rec_texts", [])
        scores = result.get("rec_scores", [])
        boxes = result.get("rec_boxes", [])
        for i, (text, score, box) in enumerate(zip(texts, scores, boxes, strict=False)):
            x1, y1, x2, y2 = (float(value) for value in box)
            recognized_text = str(text).strip()
            if not recognized_text:
                continue
            block_type = "text"
            if _looks_like_formula_line(recognized_text):
                block_type = "formula"
            blocks.append({
                "block_order": i,
                "block_type": block_type,
                "bbox": [x1, y1, x2 - x1, y2 - y1],
                "original_text": recognized_text,
                "original_latex": recognized_text if block_type == "formula" else None,
                "confidence": round(float(score), 4),
                "review_status": "pending",
            })
    return blocks


def _reading_order(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按从上到下、从左到右排序，处理同行容差。"""
    ordered = sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    if not ordered:
        return ordered
    rows: list[list[dict[str, Any]]] = []
    for block in ordered:
        _, y, _, h = block["bbox"]
        if rows and y <= max(item["bbox"][1] + item["bbox"][3] for item in rows[-1]) + 8:
            rows[-1].append(block)
        else:
            rows.append([block])
    result = [item for row in rows for item in sorted(row, key=lambda b: b["bbox"][0])]
    for i, block in enumerate(result):
        block["block_order"] = i
    return result


def _looks_like_formula_line(text: str) -> bool:
    compact = "".join(text.split())
    if len(compact) < 3:
        return False
    math_characters = sum(
        character.isdigit() or character in "=+-*/^()[]{}π∫∑√∞→≤≥<>"
        for character in compact
    )
    has_math_relation = any(marker in compact for marker in ("=", "lim", "∫", "∑", "√", "→"))
    return has_math_relation and math_characters / len(compact) >= 0.2

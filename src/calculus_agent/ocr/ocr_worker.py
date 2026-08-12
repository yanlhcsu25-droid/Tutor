#!/usr/bin/env python3
"""独立子进程 OCR 执行器 — 被 FastAPI 通过 subprocess 调用。

用法：python ocr_worker.py <image_path>
输出：JSON 到 stdout
"""

import json
import sys
import os
import multiprocessing
from time import perf_counter
from PIL import Image

# macOS 上强制 spawn 模式
if os.uname().sysname == "Darwin":
    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"status": "failed", "error": "Usage: ocr_worker.py <image_path>"}))
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.isfile(image_path):
        print(json.dumps({"status": "failed", "error": f"File not found: {image_path}"}))
        sys.exit(1)

    try:
        started_at = perf_counter()
        from paddleocr import PaddleOCR
        pipeline = PaddleOCR(
            lang="ch",
            ocr_version="PP-OCRv5",
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
            text_detection_model_dir=os.getenv("CALCULUS_AGENT_OCR_DET_MODEL_DIR") or None,
            text_recognition_model_dir=os.getenv("CALCULUS_AGENT_OCR_REC_MODEL_DIR") or None,
            text_det_limit_side_len=1600,
            text_recognition_batch_size=1,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        predictions = list(pipeline.predict(image_path))
        blocks = _convert(predictions)
        blocks = _reading_order(blocks)
        with Image.open(image_path) as image:
            image_width, image_height = image.size
        print(json.dumps({
            "status": "succeeded",
            "engine": "paddleocr",
            "duration_ms": round((perf_counter() - started_at) * 1000),
            "blocks": blocks,
            "image_width": image_width,
            "image_height": image_height,
            "warnings": [],
        }, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({
            "status": "failed",
            "engine": "paddleocr",
            "error": f"{type(error).__name__}: {error}",
            "blocks": [],
            "warnings": [f"PaddleOCR 识别失败: {error}"],
        }, ensure_ascii=False))
        sys.exit(1)


def _looks_like_formula_line(text: str) -> bool:
    compact = "".join(text.split())
    if len(compact) < 3:
        return False
    math_chars = sum(c.isdigit() or c in "=+-*/^()[]{}π∫∑√∞→≤≥<>" for c in compact)
    has_math = any(m in compact for m in ("=", "lim", "∫", "∑", "√", "→"))
    return has_math and math_chars / len(compact) >= 0.2


def _convert(predictions):
    blocks = []
    for page_idx, pred in enumerate(predictions):
        payload = pred.json
        result = payload.get("res", payload)
        texts = result.get("rec_texts", [])
        scores = result.get("rec_scores", [])
        boxes = result.get("rec_boxes", [])
        for i, (text, score, box) in enumerate(zip(texts, scores, boxes, strict=False)):
            x1, y1, x2, y2 = (float(v) for v in box)
            text = str(text).strip()
            if not text:
                continue
            bt = "formula" if _looks_like_formula_line(text) else "text"
            blocks.append({
                "block_order": i,
                "page_number": page_idx + 1,
                "block_type": bt,
                "bbox": [x1, y1, x2 - x1, y2 - y1],
                "original_text": text,
                "original_latex": text if bt == "formula" else None,
                "confidence": round(float(score), 4),
                "review_status": "pending",
            })
    return blocks


def _reading_order(blocks):
    ordered = sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    if not ordered:
        return ordered
    rows = []
    for b in ordered:
        _, y, _, h = b["bbox"]
        if rows and y <= max(it["bbox"][1] + it["bbox"][3] for it in rows[-1]) + 8:
            rows[-1].append(b)
        else:
            rows.append([b])
    result = [it for row in rows for it in sorted(row, key=lambda b: b["bbox"][0])]
    for i, b in enumerate(result):
        b["block_order"] = i
    return result


if __name__ == "__main__":
    main()

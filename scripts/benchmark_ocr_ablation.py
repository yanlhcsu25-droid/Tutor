#!/usr/bin/env python3
"""Run one OCR ablation configuration in an isolated process.

This script is diagnostic only. It does not import or mutate Workbench data.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path
from typing import Any


def _peak_rss_mb() -> float:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024 * 1024 if sys.platform == "darwin" else 1024)


def _result_payload(result: Any) -> dict[str, Any]:
    payload = result.json
    return payload.get("res", payload)


def _ocr_text(results: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    lines: list[str] = []
    blocks: list[dict[str, Any]] = []
    for result in results:
        payload = _result_payload(result)
        for text, score, box in zip(
            payload.get("rec_texts", []),
            payload.get("rec_scores", []),
            payload.get("rec_boxes", []),
            strict=False,
        ):
            value = str(text).strip()
            if not value:
                continue
            lines.append(value)
            blocks.append({"text": value, "score": float(score), "box": list(box)})
    return "\n\n".join(lines) + ("\n" if lines else ""), blocks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("ppstructure", "no_formula", "ocr_only"))
    parser.add_argument("input", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    metrics: dict[str, Any] = {
        "mode": args.mode,
        "input": str(args.input),
        "started_at": time.time(),
    }
    total_started = time.perf_counter()
    try:
        import_started = time.perf_counter()
        if args.mode == "ocr_only":
            from paddleocr import PaddleOCR
        else:
            from paddleocr import PPStructureV3
        metrics["import_seconds"] = time.perf_counter() - import_started

        init_started = time.perf_counter()
        if args.mode == "ocr_only":
            pipeline = PaddleOCR(
                lang="ch",
                ocr_version="PP-OCRv5",
                text_detection_model_name="PP-OCRv5_server_det",
                text_recognition_model_name="PP-OCRv5_server_rec",
                text_recognition_batch_size=1,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device="cpu",
            )
        else:
            pipeline = PPStructureV3(
                device="cpu",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                use_seal_recognition=False,
                use_table_recognition=False,
                use_formula_recognition=args.mode == "ppstructure",
                use_chart_recognition=False,
            )
        metrics["init_seconds"] = time.perf_counter() - init_started
        metrics["rss_after_init_mb"] = _peak_rss_mb()

        predict_started = time.perf_counter()
        results = list(pipeline.predict(input=str(args.input)))
        metrics["predict_seconds"] = time.perf_counter() - predict_started
        metrics["result_count"] = len(results)
        metrics["rss_after_predict_mb"] = _peak_rss_mb()

        save_started = time.perf_counter()
        if args.mode == "ocr_only":
            markdown, blocks = _ocr_text(results)
            (args.output_dir / "output.md").write_text(markdown, encoding="utf-8")
            (args.output_dir / "blocks.json").write_text(
                json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            metrics["block_count"] = len(blocks)
        else:
            markdown_files: list[str] = []
            for index, result in enumerate(results, start=1):
                output = args.output_dir / f"page_{index:04d}.md"
                result.save_to_markdown(output)
                markdown_files.append(str(output))
            metrics["markdown_files"] = markdown_files
        metrics["save_seconds"] = time.perf_counter() - save_started
        metrics["status"] = "succeeded"
    except Exception as error:
        metrics["status"] = "failed"
        metrics["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        metrics["total_seconds"] = time.perf_counter() - total_started
        metrics["peak_rss_mb"] = _peak_rss_mb()
        result_path = args.output_dir / "metrics.json"
        result_path.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("ABLATION_RESULT=" + json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    main()

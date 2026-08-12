"""Recover visually explicit fill-in blanks from PDF vector paths.

PPStructure serializes recognized text to Markdown, but a fill-in rule drawn as a
PDF path is not text.  This module deliberately requires both a plausible short
horizontal vector path and an OCR text box on the same baseline before adding a
placeholder.  It never infers blanks from the classified question type.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class PdfHorizontalLine:
    x1: float
    y_top: float
    x2: float
    page_width: float
    page_height: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1


def extract_pdf_horizontal_lines(
    pdf_path: str | Path,
    diagnostics: dict[int, dict[str, Any]] | None = None,
) -> dict[int, list[PdfHorizontalLine]]:
    """Return conservative fill-blank candidates, keyed by one-based page number."""
    import pypdfium2 as pdfium
    from pypdfium2 import raw

    document = pdfium.PdfDocument(str(pdf_path))
    output: dict[int, list[PdfHorizontalLine]] = {}
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            page_width, page_height = page.get_size()
            if diagnostics is not None:
                page_diagnostics = diagnostics.setdefault(page_index + 1, {})
                page_diagnostics.setdefault("raw_pdf_horizontal_lines", [])
                page_diagnostics.setdefault("candidate_blank_lines", [])
                page_diagnostics.setdefault("rejected_blank_lines", [])
            paths: list[tuple[float, float, float, float]] = []
            for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH]):
                left, bottom, right, top = (float(value) for value in obj.get_bounds())
                paths.append((left, bottom, right, top))

            verticals = [
                item for item in paths
                if item[3] - item[1] >= 8 and item[2] - item[0] <= 2
            ]
            candidates: list[PdfHorizontalLine] = []
            for left, bottom, right, top in paths:
                width = right - left
                height = top - bottom
                if width >= height:
                    raw_line = {
                        "x1": left, "y_bottom": bottom, "x2": right, "y_top": top,
                        "width": width, "height": height,
                    }
                    if diagnostics is not None:
                        diagnostics.setdefault(page_index + 1, {}).setdefault(
                            "raw_pdf_horizontal_lines", []
                        ).append(raw_line)
                # Typical answer rules are 18--180 pt.  Relative limits prevent
                # page separators and tiny minus signs from entering the pipeline.
                if not (18 <= width <= min(180, page_width * 0.32) and height <= 2):
                    if diagnostics is not None and width >= height:
                        raw_line["accepted"] = False
                        raw_line["reason"] = (
                            "too_short_or_minus_sign" if width < 18
                            else "too_long_or_page_separator" if width > min(180, page_width * 0.32)
                            else "not_thin_horizontal_line"
                        )
                    continue
                mid_y = (bottom + top) / 2
                # A horizontal path joined to vertical paths is probably a table,
                # box, or diagram rather than an answer rule.
                if any(
                    abs(edge_x - line_x) <= 2 and v_bottom - 2 <= mid_y <= v_top + 2
                    for v_left, v_bottom, v_right, v_top in verticals
                    for edge_x in (v_left, v_right)
                    for line_x in (left, right)
                ):
                    if diagnostics is not None:
                        raw_line["accepted"] = False
                        raw_line["reason"] = "connected_vertical_edge_table_or_box"
                    continue
                candidate = PdfHorizontalLine(
                    x1=left,
                    y_top=page_height - mid_y,
                    x2=right,
                    page_width=page_width,
                    page_height=page_height,
                )
                candidates.append(candidate)
                if diagnostics is not None:
                    raw_line["accepted"] = True
                    raw_line["reason"] = None
                    diagnostics.setdefault(page_index + 1, {}).setdefault(
                        "candidate_blank_lines", []
                    ).append({"source": "pdf_vector", **_line_dict(candidate)})
            if candidates:
                output[page_index + 1] = candidates
    finally:
        document.close()
    return output


def _line_dict(line: PdfHorizontalLine) -> dict[str, float]:
    return {
        "x1": line.x1, "y": line.y_top, "x2": line.x2,
        "page_width": line.page_width, "page_height": line.page_height,
    }


def _flatten_box(box: Any) -> list[float]:
    if hasattr(box, "tolist"):
        box = box.tolist()
    if not isinstance(box, (list, tuple)):
        return []
    flat: list[float] = []
    for value in box:
        if isinstance(value, (list, tuple)):
            flat.extend(float(number) for number in value)
        else:
            flat.append(float(value))
    if len(flat) < 4:
        return []
    xs, ys = flat[0::2], flat[1::2]
    return [min(xs), min(ys), max(xs), max(ys)]


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _as_mapping(result: Any) -> dict[str, Any]:
    # PaddleX Result is a dict subclass; keep its numpy ``output_img`` so the
    # exact OCR raster dimensions remain available for coordinate conversion.
    if isinstance(result, dict):
        return result
    for attribute in ("json", "to_dict"):
        value = getattr(result, attribute, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if isinstance(value, dict):
            return value
    return {}


def _ocr_text_boxes(result: Any) -> list[tuple[str, list[float]]]:
    for item in _walk(_as_mapping(result)):
        texts = item.get("rec_texts")
        boxes = item.get("rec_boxes")
        if boxes is None:
            boxes = item.get("dt_polys")
        if hasattr(boxes, "tolist"):
            boxes = boxes.tolist()
        if isinstance(texts, list) and isinstance(boxes, list) and len(texts) == len(boxes):
            found = [
                (text, flattened)
                for text, box in zip(texts, boxes)
                if isinstance(text, str) and (flattened := _flatten_box(box))
            ]
            if found:
                return found
    return []


def _ocr_page_size(result: Any) -> tuple[float, float] | None:
    """Read the raster coordinate extent used by OCR; do not estimate from content."""
    image = _ocr_image(result)
    shape = getattr(image, "shape", None)
    if shape is not None and len(shape) >= 2:
        return float(shape[1]), float(shape[0])
    for item in _walk(_as_mapping(result)):
        for width_key, height_key in (
            ("page_width", "page_height"),
            ("image_width", "image_height"),
            ("width", "height"),
        ):
            width, height = item.get(width_key), item.get(height_key)
            if isinstance(width, (int, float)) and isinstance(height, (int, float)):
                if width > 0 and height > 0:
                    return float(width), float(height)
    return None


def _ocr_image(result: Any) -> Any | None:
    for item in _walk(_as_mapping(result)):
        image = item.get("output_img")
        if image is None:
            image = item.get("input_img")
        if getattr(image, "shape", None) is not None:
            return image
    return None


def _image_horizontal_lines(
    result: Any,
    blocks: list[tuple[str, list[float]]],
    diagnostics: dict[str, Any],
) -> list[PdfHorizontalLine]:
    """Find continuous dark horizontal runs in the exact raster consumed by OCR."""
    image = _ocr_image(result)
    if image is None:
        diagnostics.setdefault("image_line_detection", {})["reason"] = "ocr_image_unavailable"
        return []
    import numpy as np

    array = np.asarray(image)
    if array.ndim == 3:
        gray = array[..., :3].mean(axis=2)
    elif array.ndim == 2:
        gray = array
    else:
        diagnostics.setdefault("image_line_detection", {})["reason"] = "unsupported_image_shape"
        return []
    height, width = gray.shape
    text_heights = [box[3] - box[1] for _, box in blocks if box[3] > box[1]]
    typical_text_height = float(np.median(text_heights)) if text_heights else 16.0
    dark = gray < 180
    min_width = max(20, int(typical_text_height * 1.2))
    max_width = int(width * 0.32)
    raw_runs: list[tuple[int, int, int]] = []
    for y in range(height):
        indices = np.flatnonzero(dark[y])
        if not len(indices):
            continue
        starts = np.r_[indices[0], indices[1:][np.diff(indices) > 1]]
        ends = np.r_[indices[:-1][np.diff(indices) > 1], indices[-1]]
        raw_runs.extend((int(x1), y, int(x2)) for x1, x2 in zip(starts, ends) if x2 - x1 + 1 >= min_width)

    # Merge anti-aliased copies of the same rule across adjacent raster rows.
    groups: list[list[tuple[int, int, int]]] = []
    for run in raw_runs:
        match = next((group for group in groups if abs(group[-1][1] - run[1]) <= 1
                      and abs(group[-1][0] - run[0]) <= 3
                      and abs(group[-1][2] - run[2]) <= 3), None)
        if match is None:
            groups.append([run])
        else:
            match.append(run)

    candidates: list[PdfHorizontalLine] = []
    rejected = diagnostics.setdefault("rejected_blank_lines", [])
    for group in groups:
        x1 = min(item[0] for item in group)
        x2 = max(item[2] for item in group)
        y1 = min(item[1] for item in group)
        y2 = max(item[1] for item in group)
        line_width, line_height = x2 - x1 + 1, y2 - y1 + 1
        record = {"source": "page_image", "x1": x1, "y1": y1, "x2": x2, "y2": y2}
        reason = None
        if line_width < min_width:
            reason = "too_short_or_minus_sign"
        elif line_width > max_width:
            reason = "too_long_or_page_separator"
        elif line_height > max(4, typical_text_height * 0.3):
            reason = "not_thin_horizontal_line"
        else:
            radius = max(6, int(typical_text_height))
            top, bottom = max(0, y1 - radius), min(height, y2 + radius + 1)
            endpoint_dark = max(
                int(dark[top:bottom, max(0, x1 - 2):min(width, x1 + 3)].sum()),
                int(dark[top:bottom, max(0, x2 - 2):min(width, x2 + 3)].sum()),
            )
            if endpoint_dark > radius * 2:
                reason = "connected_vertical_edge_table_or_box"
        if reason:
            rejected.append({**record, "reason": reason})
            continue
        candidate = PdfHorizontalLine(x1, (y1 + y2) / 2, x2, width, height)
        candidates.append(candidate)
        diagnostics.setdefault("candidate_blank_lines", []).append(
            {**record, "source": "page_image"}
        )
    diagnostics["image_line_detection"] = {
        "used": True, "raw_runs": len(raw_runs), "merged_lines": len(groups),
    }
    return candidates


def _markdown_anchor_end(markdown: str, text: str) -> int | None:
    """Locate an OCR block (or a distinctive suffix) without rewriting Markdown."""
    stripped = text.strip()
    if not stripped:
        return None
    positions = [match.end() for match in re.finditer(re.escape(stripped), markdown)]
    if len(positions) == 1:
        return positions[0]

    compact = re.sub(r"\s+", "", stripped)
    # Formula serialization often differs, while the punctuation/equality suffix
    # remains stable.  Require at least two non-space characters and uniqueness.
    for length in range(min(12, len(compact)), 1, -1):
        suffix = compact[-length:]
        matches = list(re.finditer(re.escape(suffix), markdown))
        if len(matches) == 1:
            return matches[0].end()
    return None


def restore_vector_blanks(
    markdown: str,
    result: Any,
    lines: list[PdfHorizontalLine],
    diagnostics: dict[str, Any] | None = None,
) -> str:
    """Restore blanks from vectors, falling back to the OCR page raster if absent."""
    if diagnostics is None:
        diagnostics = {}
    blocks = _ocr_text_boxes(result)
    page_size = _ocr_page_size(result)
    diagnostics["ocr_text_boxes"] = [
        {"text": text, "bbox": box} for text, box in blocks
    ]
    diagnostics.setdefault("same_row_matches", [])
    diagnostics.setdefault("restored_blank_lines", [])
    diagnostics.setdefault("rejected_blank_lines", [])
    if not blocks:
        diagnostics["restore_skipped_reason"] = "ocr_text_boxes_unavailable"
        return markdown
    if page_size is None:
        diagnostics["restore_skipped_reason"] = "ocr_page_size_unavailable"
        return markdown
    image_width, image_height = page_size
    active_lines = lines
    if not active_lines:
        diagnostics["vector_fallback_reason"] = "no_candidate_pdf_vector_lines"
        active_lines = _image_horizontal_lines(result, blocks, diagnostics)
    else:
        diagnostics["image_line_detection"] = {
            "used": False, "reason": "candidate_pdf_vector_lines_available"
        }
    insertions: set[int] = set()

    for line_index, line in enumerate(active_lines):
        lx1 = line.x1 / line.page_width * image_width
        lx2 = line.x2 / line.page_width * image_width
        ly = line.y_top / line.page_height * image_height
        same_row = [
            (text, box) for text, box in blocks
            if box[1] - max(5, (box[3] - box[1]) * 0.45)
            <= ly
            <= box[3] + max(5, (box[3] - box[1]) * 0.45)
        ]
        match_record: dict[str, Any] = {
            "line_index": line_index,
            "line": {"x1": lx1, "y": ly, "x2": lx2},
            "matches": [{"text": text, "bbox": box} for text, box in same_row],
        }
        diagnostics["same_row_matches"].append(match_record)
        before = [item for item in same_row if item[1][2] <= lx1 + 8]
        if not before:
            match_record["accepted"] = False
            match_record["reason"] = "no_text_before_line_on_same_row"
            diagnostics["rejected_blank_lines"].append({
                "line_index": line_index, "reason": match_record["reason"]
            })
            continue
        text, box = max(before, key=lambda item: item[1][2])
        gap = lx1 - box[2]
        text_height = max(1, box[3] - box[1])
        if gap < -4 or gap > max(80, text_height * 5):
            match_record["accepted"] = False
            match_record["reason"] = "text_to_line_gap_out_of_range"
            diagnostics["rejected_blank_lines"].append({
                "line_index": line_index, "reason": match_record["reason"], "gap": gap
            })
            continue
        # If another text block exists to the right, require the line to end before
        # it. This supports blanks between clauses without guessing their location.
        after = [item for item in same_row if item[1][0] >= lx1 - 4]
        if after and lx2 > min(item[1][0] for item in after) + 4:
            match_record["accepted"] = False
            match_record["reason"] = "line_overlaps_text_on_right"
            diagnostics["rejected_blank_lines"].append({
                "line_index": line_index, "reason": match_record["reason"]
            })
            continue
        anchor = _markdown_anchor_end(markdown, text)
        if anchor is None:
            match_record["accepted"] = False
            match_record["reason"] = "unique_markdown_anchor_not_found"
            diagnostics["rejected_blank_lines"].append({
                "line_index": line_index, "reason": match_record["reason"], "ocr_text": text
            })
            continue
        if markdown[anchor:anchor + 4] == "____":
            match_record["accepted"] = False
            match_record["reason"] = "blank_already_present"
            continue
        insertions.add(anchor)
        match_record["accepted"] = True
        match_record["reason"] = None
        diagnostics["restored_blank_lines"].append({
            "line_index": line_index, "markdown_offset": anchor, "ocr_text": text
        })

    for position in sorted(insertions, reverse=True):
        markdown = f"{markdown[:position]}____{markdown[position:]}"
    return markdown

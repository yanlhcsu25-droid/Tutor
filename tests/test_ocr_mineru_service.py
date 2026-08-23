import asyncio
import io

from PIL import Image

import calculus_agent.ocr.service as service
from calculus_agent.ocr.service import create_ocr_task_async, get_ocr_task


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(output, format="PNG")
    return output.getvalue()


def test_image_upload_uses_mineru_and_persists_markdown_blocks(session, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(
        service,
        "_run_mineru_pages",
        lambda _path: ([(1, "# 第一题\n求极限。\n")], {"elapsed_seconds": 0.2}),
    )

    task = asyncio.run(create_ocr_task_async(session, _png_bytes(), "question.png"))
    result = get_ocr_task(session, task.id)

    assert result is not None
    assert result["engine"] == "mineru"
    assert result["status"] == "completed"
    assert result["blocks"][0]["block_type"] == "markdown"
    assert "求极限" in result["blocks"][0]["original_text"]

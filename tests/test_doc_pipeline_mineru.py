import calculus_agent.ocr.doc_pipeline as pipeline


def test_document_pipeline_uses_mineru_markdown(monkeypatch, tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-test")
    monkeypatch.setattr(
        pipeline,
        "run_mineru",
        lambda _pdf, _output: ([
            {
                "page_idx": 0,
                "type": "text",
                "text": "## 一、计算题\n1. 求极限。",
            }
        ], {}),
    )

    candidates = pipeline.parse_pdf_to_candidates(str(pdf))

    assert len(candidates) == 1
    assert candidates[0].body == "求极限。"
    assert candidates[0].page_number == 1

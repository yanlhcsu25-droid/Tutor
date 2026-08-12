from calculus_agent.ocr import doc_pipeline


def test_legacy_doc_import_delegates_to_unified_cross_page_splitter(monkeypatch):
    monkeypatch.setattr(
        doc_pipeline,
        "run_ppstructure",
        lambda _path: [
            "1. 第一题\n\n2. 第二题跨页开始",
            "第二题跨页结束\n\n3. 第三题",
        ],
    )

    candidates = doc_pipeline.parse_pdf_to_candidates("fake.pdf")

    assert [item.original_number for item in candidates] == ["1", "2", "3"]
    assert candidates[1].page_number == 1
    assert "第二题跨页开始" in candidates[1].body
    assert "第二题跨页结束" in candidates[1].body

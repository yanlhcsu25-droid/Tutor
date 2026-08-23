# MinerU OCR Acceptance

Validated locally against the real `.venv-mineru/bin/mineru` executable after
removing Paddle-based OCR paths.

| Input | Result | Elapsed |
| --- | --- | ---: |
| 1-page calculus PDF | 5 blocks, 1/1 non-empty Markdown page | 12.0 s |
| PNG converted to PDF | 4 blocks, 1/1 non-empty Markdown page | 11.2 s |
| 2-page question/solution paper | 6 candidates, 5 matched, 1 retained for review | 40.6 s |

The final case used the deterministic `DocumentLayout("separate", [1], [2])`
matcher. The unmatched candidate was not silently accepted, confirming that OCR
success does not bypass review safety.

Automated coverage:

```bash
uv run pytest -q tests/test_ocr_mineru_service.py \
  tests/test_doc_pipeline_mineru.py \
  tests/test_mineru_adapter.py \
  tests/test_ocr_fast_pipeline.py
```

Full Python regression and frontend build also pass:

```bash
uv run pytest -q
npm --prefix web run build
```

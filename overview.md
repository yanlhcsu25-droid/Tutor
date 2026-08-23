# Tutor OCR Overview

Tutor uses one OCR backend: **MinerU**.

```text
Image/PDF upload
  → image-to-PDF conversion when needed
  → MinerU CLI
  → page-indexed Markdown
  → shared deterministic question/answer splitter
  → reviewable drafts
  → publish
```

Both API OCR endpoints and Workbench PDF import use
`calculus_agent.ocr.mineru_adapter`. Re-splitting edited Markdown only reruns the
deterministic parser and does not invoke OCR again.

The frontend no longer exposes an OCR-engine selector. MinerU progress stages
are projected as layout, recognition, OCR detection, page output, and matching.

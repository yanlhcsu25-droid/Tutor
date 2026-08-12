#!/usr/bin/env python3
"""Normalize existing non-published OCR selection drafts without rerunning OCR.

Dry-run is the default. Apply updates only option-line presentation in the two
stored Markdown variants and refreshes validation metadata; review status and all
other manually edited bytes remain unchanged.
"""

from __future__ import annotations

import argparse

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from calculus_agent.config import get_settings
from calculus_agent.models import OcrImportDraft
from calculus_agent.workbench.markdown_schema import (
    normalize_selection_option_markdown,
    parse_markdown,
    payload_from_markdown,
)


def is_selection(markdown: str) -> bool:
    return parse_markdown(markdown).sections.get("题型", "").strip() in {
        "selection", "single_choice", "multiple_choice",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--database-url", default=get_settings().database_url)
    args = parser.parse_args()
    if args.apply and not args.yes:
        parser.error("--apply requires --yes")

    engine = create_engine(args.database_url)
    with Session(engine) as session:
        drafts = list(session.scalars(
            select(OcrImportDraft).where(OcrImportDraft.review_status != "published")
        ).all())
        selected = [draft for draft in drafts if is_selection(draft.edited_markdown)]
        markdown_changes = 0
        validation_changes = 0
        for draft in selected:
            new_ocr = normalize_selection_option_markdown(draft.ocr_markdown)
            new_edited = normalize_selection_option_markdown(draft.edited_markdown)
            if new_ocr != draft.ocr_markdown or new_edited != draft.edited_markdown:
                markdown_changes += 1

            _, validation = payload_from_markdown(
                new_edited,
                question_id=draft.id,
                source_file_id=draft.source_id,
                ocr_markdown=new_ocr,
                source_bbox=draft.bbox_json,
            )
            new_validation = validation.model_dump(mode="json")
            if new_validation != draft.validation_json:
                validation_changes += 1

            if args.apply:
                draft.ocr_markdown = new_ocr
                draft.edited_markdown = new_edited
                draft.validation_json = new_validation

        print(f"non_published_selection_drafts={len(selected)}")
        print(f"markdown_changes={markdown_changes}")
        print(f"validation_changes={validation_changes}")
        if args.apply:
            session.commit()
            print("APPLIED: review_status unchanged; no OCR executed")
        else:
            session.rollback()
            print("DRY-RUN: database unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

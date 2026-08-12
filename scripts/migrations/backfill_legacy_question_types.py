#!/usr/bin/env python3
"""One-off migration for legacy question_type="other" records.

New imports use the shared classifier directly. The default mode is a strictly
read-only dry-run. Apply is intentionally guarded and never edits OCR Markdown.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from calculus_agent.config import get_settings
from calculus_agent.models import OcrImportDraft, Question, QuestionDraft
from calculus_agent.ocr.import_service import parse_markdown_to_parts
from calculus_agent.workbench.question_type_classifier import infer_question_type


@dataclass
class BackfillRow:
    entity: str
    question_id: str
    source_file_id: str
    page_number: int | str
    original_number: str
    status: str
    old_type: str
    new_type: str
    reason: str
    needs_review: bool
    question_preview: str


FIELDS = list(BackfillRow.__dataclass_fields__)
TYPE_SECTION_RE = re.compile(r"(?m)(^##\s+题型\s*\n+)([^\n]+)")


def _preview(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:180]


def _classify(text: str):
    return infer_question_type(text)


def collect_rows(session: Session) -> list[BackfillRow]:
    rows: list[BackfillRow] = []
    ocr_by_id = {item.id: item for item in session.scalars(select(OcrImportDraft)).all()}

    for draft in ocr_by_id.values():
        parts = parse_markdown_to_parts(draft.edited_markdown)
        old_type = parts.get("题型", "").strip()
        if old_type != "other":
            continue
        content = parts.get("题目内容") or parts.get("题目", "")
        result = _classify(content)
        rows.append(BackfillRow(
            "ocr_import_draft", draft.id, draft.source_id, draft.page_number,
            draft.original_number, draft.review_status, old_type,
            result.question_type, result.reason, result.needs_review, _preview(content),
        ))

    drafts = list(session.scalars(
        select(QuestionDraft).where(QuestionDraft.question_type == "other")
    ).all())
    for draft in drafts:
        result = _classify(draft.question_text)
        source = ocr_by_id.get(draft.source_item_id) if draft.source_name == "ocr_import" else None
        rows.append(BackfillRow(
            "question_draft", draft.id,
            source.source_id if source else draft.source_name,
            source.page_number if source else "",
            source.original_number if source else draft.source_item_id,
            draft.status, draft.question_type, result.question_type, result.reason,
            result.needs_review, _preview(draft.question_text),
        ))

    questions = list(session.scalars(
        select(Question).where(Question.question_type == "other")
    ).all())
    drafts_by_id = {item.id: item for item in session.scalars(
        select(QuestionDraft).where(QuestionDraft.id.in_([item.draft_id for item in questions]))
    ).all()} if questions else {}
    for question in questions:
        draft = drafts_by_id.get(question.draft_id)
        source = ocr_by_id.get(draft.source_item_id) if draft and draft.source_name == "ocr_import" else None
        result = _classify(question.question_text)
        rows.append(BackfillRow(
            "question", question.id,
            source.source_id if source else (draft.source_name if draft else ""),
            source.page_number if source else "",
            source.original_number if source else (draft.source_item_id if draft else ""),
            question.review_status, question.question_type, result.question_type,
            result.reason, result.needs_review, _preview(question.question_text),
        ))
    return rows


def write_csv(rows: list[BackfillRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def replace_markdown_question_type(markdown: str, new_type: str) -> str:
    """Replace only an exact legacy `## 题型` value; preserve all other bytes."""
    replaced = 0

    def replacement(match: re.Match[str]) -> str:
        nonlocal replaced
        if match.group(2).strip() != "other":
            return match.group(0)
        replaced += 1
        leading = match.group(2)[:len(match.group(2)) - len(match.group(2).lstrip())]
        trailing = match.group(2)[len(match.group(2).rstrip()):]
        return f"{match.group(1)}{leading}{new_type}{trailing}"

    output = TYPE_SECTION_RE.sub(replacement, markdown)
    if replaced != 1:
        raise ValueError(f"expected exactly one legacy type section, found {replaced}")
    return output


def apply_safe_columns(session: Session, rows: list[BackfillRow]) -> Counter[str]:
    """Update only dedicated type columns; OCR Markdown is deliberately immutable."""
    changed: Counter[str] = Counter()
    for row in rows:
        if row.new_type == "unknown":
            continue
        if row.entity == "ocr_import_draft":
            item = session.get(OcrImportDraft, row.question_id)
            if item is not None:
                item.ocr_markdown = replace_markdown_question_type(item.ocr_markdown, row.new_type)
                item.edited_markdown = replace_markdown_question_type(item.edited_markdown, row.new_type)
                if item.validation_json:
                    validation = dict(item.validation_json)
                    parsed = dict(validation.get("parsed") or {})
                    parsed["question_type"] = row.new_type
                    validation["parsed"] = parsed
                    item.validation_json = validation
                changed[row.entity] += 1
        elif row.entity == "question_draft":
            item = session.get(QuestionDraft, row.question_id)
            if item is not None and item.question_type == "other":
                item.question_type = row.new_type
                metadata = dict(item.proposed_classification_json or {})
                metadata["question_type_backfill"] = {
                    "old_type": row.old_type, "new_type": row.new_type,
                    "reason": row.reason, "needs_review": row.needs_review,
                }
                item.proposed_classification_json = metadata
                changed[row.entity] += 1
        elif row.entity == "question":
            item = session.get(Question, row.question_id)
            if item is not None and item.question_type == "other":
                item.question_type = row.new_type
                changed[row.entity] += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="read only (default)")
    mode.add_argument("--apply", action="store_true", help="apply safe dedicated-column updates")
    parser.add_argument("--yes", action="store_true", help="required with --apply")
    parser.add_argument("--database-url", default=get_settings().database_url)
    parser.add_argument(
        "--csv", type=Path,
        default=Path("output/question_type_backfill_dry_run.csv"),
    )
    args = parser.parse_args()
    if args.apply and not args.yes:
        parser.error("--apply requires --yes; inspect the dry-run CSV first")

    engine = create_engine(args.database_url)
    with Session(engine) as session:
        rows = collect_rows(session)
        write_csv(rows, args.csv)
        counts = Counter(row.new_type for row in rows)
        entities = Counter(row.entity for row in rows)
        print(f"历史 other 记录总数：{len(rows)}")
        print("实体统计：" + "，".join(f"{key}={value}" for key, value in sorted(entities.items())))
        for name in ("selection", "fill_blank", "calculation", "proof", "subjective", "unknown"):
            print(f"{name:<12} {counts[name]}")
        print(f"CSV：{args.csv.resolve()}")
        unknown = [row for row in rows if row.needs_review]
        if unknown:
            print("需要人工判断的样本：")
            for row in unknown[:20]:
                print(f"- {row.entity} {row.question_id}: {row.question_preview}")
        print("注意：apply 仅替换 OcrImportDraft Markdown 的 `## 题型` 值，并同步已有 validation metadata。")
        if args.apply:
            changed = apply_safe_columns(session, rows)
            session.commit()
            print("已更新：" + "，".join(f"{key}={value}" for key, value in sorted(changed.items())))
        else:
            session.rollback()
            print("DRY-RUN：数据库未作任何修改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.models import QuestionDraft
from calculus_agent.schemas import DatasetImportSummary


SOURCE_NAME = "UGMathBench"


def import_ugmathbench(
    session: Session,
    path: Path,
    *,
    variants: list[int],
    limit: int | None = None,
) -> DatasetImportSummary:
    records = json.loads(path.read_text(encoding="utf-8-sig"))
    created = existing = skipped = 0
    processed = 0
    for record in records:
        if limit is not None and processed >= limit:
            break
        for variant in sorted(set(variants)):
            if variant not in {1, 2, 3}:
                skipped += 1
                continue
            question = _clean(record.get(f"problem_v{variant}"))
            answers = record.get(f"answer_v{variant}") or []
            if not question or not answers:
                skipped += 1
                continue
            source_item_id = str(record["id"])
            found = session.scalar(
                select(QuestionDraft).where(
                    QuestionDraft.source_name == SOURCE_NAME,
                    QuestionDraft.source_item_id == source_item_id,
                    QuestionDraft.variant == variant,
                )
            )
            if found is not None:
                existing += 1
                processed += 1
                continue
            session.add(
                QuestionDraft(
                    source_name=SOURCE_NAME,
                    source_item_id=source_item_id,
                    variant=variant,
                    subject=str(record.get("subject") or "Calculus_-_single_variable"),
                    source_topic=_clean(record.get("topic")),
                    source_subtopic=_clean(record.get("subtopic")),
                    question_text=question,
                    reference_answers_json=[str(item) for item in answers],
                    answer_types_json=record.get(f"answer_type_v{variant}") or [],
                    options_json=record.get(f"options_v{variant}") or [],
                    level=_clean(record.get("level")),
                    keywords_json=record.get("keywords") or [],
                    normalized_fingerprint=_fingerprint(question),
                )
            )
            created += 1
            processed += 1
    session.flush()
    return DatasetImportSummary(created=created, existing=existing, skipped=skipped)


def _clean(value) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", "", text).lower()
    normalized = re.sub(r"[，。；：、,.!?？！]", "", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

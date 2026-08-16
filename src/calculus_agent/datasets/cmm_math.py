import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.datasets.mm_math import _fingerprint, _publish_trusted, _records
from calculus_agent.models import KnowledgeNode, Question, QuestionDraft, QuestionKnowledgeLink
from calculus_agent.schemas import DatasetImportSummary

SOURCE_NAME = "CMM-Math"
DEFAULT_LEVELS = ("七年级", "八年级", "九年级")


def import_cmm_math(
    session: Session,
    path: Path,
    *,
    levels: tuple[str, ...] = DEFAULT_LEVELS,
    image_root: Path | None = None,
    text_only: bool = True,
    require_analysis: bool = True,
    limit: int | None = None,
    publish: bool = True,
) -> DatasetImportSummary:
    created = existing = skipped = 0
    for index, record in enumerate(_records(path)):
        if limit is not None and created + existing >= limit:
            break
        if str(record.get("level") or "") not in levels:
            continue

        question = _clean(record.get("question"))
        answer = _clean(record.get("answer"))
        analysis = _clean(record.get("analysis"))
        images = _images(record.get("image"))
        options = _options(record.get("options"))
        visible_text = "\n".join(filter(None, [question, "\n".join(options), analysis]))
        if (
            not question
            or not answer
            or (require_analysis and not analysis)
            or (text_only and (images or "<ImageHere>" in visible_text))
        ):
            skipped += 1
            continue

        source_item_id = str(record.get("id") or index)
        found = session.scalar(
            select(QuestionDraft).where(
                QuestionDraft.source_name == SOURCE_NAME,
                QuestionDraft.source_item_id == source_item_id,
                QuestionDraft.variant == 1,
            )
        )
        if found:
            existing += 1
            continue

        question_type = _question_type(question, options, answer)
        question_text = question
        if options:
            question_text = f"{question}\n" + "\n".join(options)
        subject = _clean(record.get("subject"))
        knowledge = _knowledge_names(subject, question, answer, analysis)
        solution = analysis or f"参考答案：{answer}"
        draft = QuestionDraft(
            source_name=SOURCE_NAME,
            source_item_id=source_item_id,
            variant=1,
            subject="初中数学",
            language="zh-CN",
            grade=str(record["level"]),
            question_type=question_type,
            source_topic=subject,
            source_subtopic=None,
            question_text=question_text,
            reference_answers_json=[answer],
            answer_types_json=["choice" if question_type == "选择题" else "text"],
            options_json=options,
            solution_text=solution,
            image_path=_image_paths(images, image_root),
            level=str(record["level"]),
            keywords_json=knowledge,
            normalized_fingerprint=_fingerprint(question_text),
            status="approved" if publish else "pending",
        )
        session.add(draft)
        session.flush()
        if publish:
            _publish_trusted(
                session,
                draft,
                knowledge,
                answer,
                solution,
                source_name=SOURCE_NAME,
            )
        created += 1
    session.flush()
    return DatasetImportSummary(created=created, existing=existing, skipped=skipped)


def backfill_cmm_math_knowledge(session: Session) -> dict[str, int]:
    """Add explicit concepts found in trusted CMM-Math text to published questions."""
    added = scanned = 0
    rows = session.execute(
        select(QuestionDraft, Question)
        .join(Question, Question.draft_id == QuestionDraft.id)
        .where(QuestionDraft.source_name == SOURCE_NAME)
    ).all()
    for draft, question in rows:
        scanned += 1
        names = _knowledge_names(
            draft.source_topic,
            draft.question_text,
            " ".join(draft.reference_answers_json or []),
            draft.solution_text,
        )
        for name in names[1:] if draft.source_topic else names:
            normalized = normalize_name(name)
            node = session.scalar(
                select(KnowledgeNode).where(
                    KnowledgeNode.node_type == "concept",
                    KnowledgeNode.normalized_name == normalized,
                )
            )
            if node is None:
                node = KnowledgeNode(
                    node_type="concept",
                    name=name,
                    normalized_name=normalized,
                    source_type="trusted_dataset",
                    confidence=1.0,
                    review_status="approved",
                )
                session.add(node)
                session.flush()
            exists = session.scalar(
                select(QuestionKnowledgeLink).where(
                    QuestionKnowledgeLink.question_id == question.id,
                    QuestionKnowledgeLink.knowledge_node_id == node.id,
                )
            )
            if exists is None:
                session.add(
                    QuestionKnowledgeLink(
                        question_id=question.id,
                        knowledge_node_id=node.id,
                        relation_type="secondary_concept",
                        confidence=1.0,
                        evidence_json=["CMM-Math 题干/解析显式术语"],
                    )
                )
                added += 1
    session.flush()
    return {"scanned": scanned, "added": added}


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() == "null" else text


def _knowledge_names(subject: str | None, *texts: str | None) -> list[str]:
    names = [subject] if subject else []
    content = "\n".join(text for text in texts if text)
    for concept in ("一次函数", "二次函数", "反比例函数", "正比例函数"):
        if concept in content and concept not in names:
            names.append(concept)
    return names


def _images(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _options(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) > 1:
        return lines
    return [
        item.strip()
        for item in re.split(r"(?=\b[A-D][.、．]\s*)", value)
        if item.strip()
    ]


def _question_type(question: str, options: list[str], answer: str) -> str:
    if options or re.fullmatch(r"[A-D]+", answer.strip(), re.I):
        return "选择题"
    if re.search(r"\\qquad|_{3,}|填空|横线|空格|填入", question):
        return "填空题"
    return "计算题"


def _image_paths(images: list[str], image_root: Path | None) -> str | None:
    if not images:
        return None
    paths = [str(image_root / image) if image_root else image for image in images]
    return json.dumps(paths, ensure_ascii=False)

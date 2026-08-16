import hashlib
import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.models import KnowledgeNode, Question, QuestionDraft, QuestionKnowledgeLink
from calculus_agent.schemas import DatasetImportSummary

SOURCE_NAME = "MM-Math"


def import_mm_math(
    session: Session,
    path: Path,
    *,
    image_root: Path | None = None,
    limit: int | None = None,
    publish: bool = True,
) -> DatasetImportSummary:
    created = existing = skipped = 0
    for index, record in enumerate(_records(path)):
        if limit is not None and created + existing >= limit:
            break
        question = _text(record, "question", "problem")
        solution = _text(record, "solution", "answer_analysis", "analysis")
        if not question or not solution:
            skipped += 1
            continue
        source_item_id = str(
            record.get("id") or record.get("qid") or record.get("file_name") or index
        )
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
        knowledge = _knowledge(record.get("knowledge") or record.get("knowledge_points"))
        image = _text(record, "file_name", "image", "image_path", "img")
        resolved_image = str(image_root / image) if image_root and image else image
        answer = _text(record, "final_answer", "answer") or _extract_final_answer(solution)
        draft = QuestionDraft(
            source_name=SOURCE_NAME,
            source_item_id=source_item_id,
            variant=1,
            subject="初中数学",
            language=_language(question),
            grade=None,
            question_type=_question_type(record, question),
            source_topic=knowledge[0] if knowledge else None,
            source_subtopic=knowledge[1] if len(knowledge) > 1 else None,
            question_text=question,
            reference_answers_json=[answer] if answer else [],
            answer_types_json=["text"],
            options_json=[],
            solution_text=solution,
            image_path=resolved_image,
            level=str(record.get("year") or "") or None,
            keywords_json=knowledge,
            normalized_fingerprint=_fingerprint(question),
            status="approved" if publish else "pending",
        )
        session.add(draft)
        session.flush()
        if publish:
            _publish_trusted(session, draft, knowledge, answer, solution)
        created += 1
    session.flush()
    return DatasetImportSummary(created=created, existing=existing, skipped=skipped)


def _publish_trusted(
    session: Session,
    draft: QuestionDraft,
    knowledge_names: list[str],
    answer: str | None,
    solution: str,
    *,
    source_name: str = SOURCE_NAME,
) -> None:
    question = Question(
        draft_id=draft.id,
        question_text=draft.question_text,
        grade=draft.grade,
        question_type=draft.question_type,
        final_answer=answer,
        solution_json={"solution_steps": [solution], "source": source_name},
        verification_status="dataset_reference",
        review_status="approved",
    )
    session.add(question)
    session.flush()
    for index, name in enumerate(dict.fromkeys(knowledge_names)):
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
        session.add(
            QuestionKnowledgeLink(
                question_id=question.id,
                knowledge_node_id=node.id,
                relation_type="primary_concept" if index == 0 else "secondary_concept",
                confidence=1.0,
                evidence_json=[f"{source_name} 标注"],
            )
        )


def _records(path: Path):
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        for line in text.splitlines():
            if line.strip():
                yield json.loads(line)
        return
    data = json.loads(text)
    yield from data if isinstance(data, list) else data.get("data", [])


def _text(record: dict, *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，;；/|]", value) if item.strip()]
    return []


def _knowledge(value) -> list[str]:
    if isinstance(value, dict):
        ordered_keys = sorted(
            value,
            key=lambda key: (
                0 if re.fullmatch(r"level_\d+", str(key)) else 1,
                str(key),
            ),
        )
        return [str(value[key]).strip() for key in ordered_keys if str(value[key]).strip()]
    return _list(value)


def _question_type(record: dict, question: str) -> str:
    explicit = _text(record, "question_type", "type")
    if explicit:
        return explicit
    if re.search(r"[A-D][\.、．]", question):
        return "选择题"
    if "____" in question or "填空" in question or "\\underline" in question:
        return "填空题"
    return "计算题"


def _extract_final_answer(solution: str) -> str | None:
    boxed = _last_boxed_value(solution)
    if boxed:
        return boxed
    matches = re.findall(r"(?:答案|故|所以|因此)[：:]?\s*([^。；\n]+)", solution)
    return matches[-1].strip() if matches else None


def _last_boxed_value(text: str) -> str | None:
    marker = r"\boxed{"
    start = text.rfind(marker)
    if start < 0:
        return None
    index = start + len(marker)
    depth = 1
    result: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                value = "".join(result).strip()
                return value or None
        result.append(char)
        index += 1
    return None


def _language(text: str) -> str:
    return "zh-CN" if re.search(r"[\u4e00-\u9fff]", text) else "en-US"


def _fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+|[，。；：、,.!?？！]", "", text).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

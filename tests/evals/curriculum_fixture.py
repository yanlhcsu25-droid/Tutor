"""Deterministic, isolated curriculum fixture for regression evals.

The eval runner uses an isolated (empty) SQLite session per case. Generation
cases that reference a chapter scope (e.g. ``第三章``) would otherwise resolve
against an empty curriculum and return ``scope_not_found``. This module seeds a
small *canonical* curriculum that mirrors the real production structure:

* the chapter ``code`` keeps the Chinese numeral (``一`` / ``二`` / ``三``),
* the ``title`` carries the human label (``第三章 微分中值定理与导数的应用``),

so the scope resolver behaves identically to production **without** depending on
a developer's local ``calculus_agent.db``. The fixture is deterministic,
isolated, repeatable, and CI-runnable.
"""

from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook

# Fixed ids keep the fixture deterministic and repeatable across runs.
EVAL_TEXTBOOK_ID = "eval-textbook-canonical"

# Canonical chapters shared by the generation regression cases.
# Chapter 4 is intentionally absent: GEN-01 exercises the missing-scope path.
EVAL_CHAPTERS = (
    {
        "code": "一",
        "title": "第一章 函数与极限",
        "knowledge": ("函数与极限", "极限的运算法则"),
    },
    {
        "code": "二",
        "title": "第二章 导数与微分",
        "knowledge": ("导数的概念", "基本导数公式"),
    },
    {
        "code": "三",
        "title": "第三章 微分中值定理与导数的应用",
        "knowledge": ("微分中值定理", "洛必达法则", "导数的应用"),
    },
)


def seed_eval_curriculum(session) -> None:
    """Seed the canonical eval curriculum into the given (isolated) session.

    Idempotent within a single session: if the canonical textbook already
    exists the seed is skipped, so repeated calls or shared sessions are safe.
    """
    if session.get(Textbook, EVAL_TEXTBOOK_ID) is not None:
        return

    textbook = Textbook(
        id=EVAL_TEXTBOOK_ID,
        name="高等数学（上册）",
        edition="eval-canonical",
        is_active=True,
    )
    session.add(textbook)

    for index, chapter in enumerate(EVAL_CHAPTERS, start=1):
        chapter_node = CurriculumNode(
            id=f"eval-chapter-{index}",
            textbook_id=EVAL_TEXTBOOK_ID,
            parent_id=None,
            node_type="chapter",
            code=chapter["code"],
            title=chapter["title"],
            sort_order=index,
            source="eval_fixture",
            review_status="approved",
        )
        session.add(chapter_node)
        for k_index, knowledge_name in enumerate(chapter["knowledge"], start=1):
            session.add(KnowledgeNode(
                id=f"eval-knowledge-{index}-{k_index}",
                curriculum_node_id=chapter_node.id,
                node_type="concept",
                name=knowledge_name,
                normalized_name=normalize_name(knowledge_name),
                description=f"eval fixture knowledge node for {knowledge_name}",
                source_type="eval_fixture",
                confidence=1.0,
                review_status="approved",
            ))

    session.flush()

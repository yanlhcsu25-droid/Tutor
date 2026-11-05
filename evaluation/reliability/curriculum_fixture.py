"""Deterministic, isolated curriculum fixture for regression evals.

The eval runner uses an isolated (empty) SQLite session per case. Generation
cases that reference a chapter scope would otherwise resolve against an empty
curriculum. This module seeds a small canonical curriculum that mirrors the
production hierarchy while remaining deterministic and CI-runnable.
"""

from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.models import CurriculumNode, KnowledgeNode, Textbook


EVAL_TEXTBOOK_ID = "eval-textbook-canonical"

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

# Keep descriptions semantically realistic because Scope Capability Eval
# deliberately evaluates hybrid retrieval. In particular, a generic "极限"
# query should have a real semantic path into chapter 3 through L'Hopital,
# rather than seeing chapter 3 only because top-k happens to include zero-score
# candidates.
EVAL_KNOWLEDGE_DESCRIPTIONS = {
    "函数与极限": "函数、数列极限与函数极限的基本概念。",
    "极限的运算法则": "极限的四则运算、复合运算与常见极限计算。",
    "导数的概念": "导数定义、变化率与可导性。",
    "基本导数公式": "常见初等函数的导数公式与求导法则。",
    "微分中值定理": "罗尔定理、拉格朗日中值定理及其应用。",
    "洛必达法则": "利用导数处理未定式极限，是导数应用中的极限计算方法。",
    "导数的应用": "利用导数研究单调性、极值、最值与函数图形。",
}


def seed_eval_curriculum(session) -> None:
    """Seed the canonical eval curriculum into an isolated session."""
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

        for k_index, knowledge_name in enumerate(
            chapter["knowledge"],
            start=1,
        ):
            session.add(
                KnowledgeNode(
                    id=f"eval-knowledge-{index}-{k_index}",
                    curriculum_node_id=chapter_node.id,
                    node_type="concept",
                    name=knowledge_name,
                    normalized_name=normalize_name(knowledge_name),
                    description=EVAL_KNOWLEDGE_DESCRIPTIONS.get(
                        knowledge_name,
                        f"eval fixture knowledge node for {knowledge_name}",
                    ),
                    source_type="eval_fixture",
                    confidence=1.0,
                    review_status="approved",
                )
            )

    session.flush()

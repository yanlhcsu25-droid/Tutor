from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.models import KnowledgeAlias, KnowledgeNode, Question, QuestionKnowledgeLink, QuestionKnowledgeReview, CurriculumNode, Textbook


@dataclass(frozen=True)
class TaxonomyItem:
    name: str
    aliases: tuple[str, ...]
    keywords: tuple[str, ...]


CALCULUS_TAXONOMY = (
    TaxonomyItem("函数极限", ("极限",), (r"\lim", "函数极限", "趋于")),
    TaxonomyItem("数列极限", (), (r"n\to", "数列极限", "n趋于")),
    TaxonomyItem("左右极限", ("单侧极限",), (r"^-", r"^+", "左极限", "右极限")),
    TaxonomyItem("极限运算法则", (), ("极限运算法则", "极限号", "商的极限", "积的极限")),
    TaxonomyItem("两个重要极限", ("重要极限",), (r"\frac{\sin", r"1+\frac{1}", "重要极限")),
    TaxonomyItem("无穷小与无穷大", ("无穷小", "无穷大"), ("无穷小", "无穷大", "等价无穷小")),
    TaxonomyItem("函数连续性", ("连续",), ("连续", "连续函数", "连续区间")),
    TaxonomyItem("间断点及其分类", ("间断点",), ("间断点", "跳跃间断", "可去间断", "无穷间断")),
    TaxonomyItem("导数定义", ("导数",), ("导数定义", "可导", "导数")),
    TaxonomyItem("复合函数求导", ("链式法则",), ("复合函数求导", "链式法则")),
    TaxonomyItem("隐函数与参数方程求导", ("隐函数求导",), ("隐函数", "参数方程", "对数求导")),
    TaxonomyItem("微分中值定理", ("中值定理",), ("罗尔定理", "拉格朗日", "柯西中值")),
    TaxonomyItem("洛必达法则", (), ("洛必达", "L'Hospital")),
    TaxonomyItem("泰勒公式", ("泰勒展开",), ("泰勒", "麦克劳林", "展开式")),
    TaxonomyItem("函数单调性与极值", ("单调性", "极值"), ("单调", "极值", "驻点")),
    TaxonomyItem("函数凹凸性与拐点", ("凹凸性",), ("凹凸", "拐点")),
    TaxonomyItem("不定积分", (), ("不定积分", "原函数", r"\int")),
    TaxonomyItem("定积分", (), ("定积分", "积分上限", "积分下限")),
    TaxonomyItem("换元积分法", ("换元法",), ("换元积分", "令", "变量代换")),
    TaxonomyItem("分部积分法", ("分部积分",), ("分部积分",)),
    TaxonomyItem("反常积分", ("广义积分",), ("反常积分", "广义积分")),
)


def ensure_calculus_taxonomy(session: Session) -> list[KnowledgeNode]:
    existing = {
        node.normalized_name: node
        for node in session.scalars(select(KnowledgeNode)).all()
    }
    output: list[KnowledgeNode] = []
    for item in CALCULUS_TAXONOMY:
        normalized = normalize_name(item.name)
        node = existing.get(normalized)
        if node is None:
            node = KnowledgeNode(
                node_type="knowledge_point",
                name=item.name,
                normalized_name=normalized,
                description="高等数学受控知识点",
                source_type="system_taxonomy",
                confidence=1.0,
                review_status="approved",
            )
            session.add(node)
            session.flush()
            for alias in item.aliases:
                session.add(KnowledgeAlias(
                    node_id=node.id,
                    alias=alias,
                    normalized_alias=normalize_name(alias),
                ))
            existing[normalized] = node
        output.append(node)
    session.flush()
    return output


def suggest_question_knowledge(session: Session, question: Question, *, limit: int = 5) -> list[dict]:
    nodes = {node.name: node for node in session.scalars(select(KnowledgeNode).join(CurriculumNode, KnowledgeNode.curriculum_node_id == CurriculumNode.id).join(Textbook, CurriculumNode.textbook_id == Textbook.id).where(Textbook.is_active.is_(True), CurriculumNode.node_type == "section", KnowledgeNode.review_status == "approved")).all()}
    # Backward compatibility for legacy taxonomy-only records. New imports
    # always use the directory-constrained branch above.
    if not nodes:
        nodes = {node.name: node for node in ensure_calculus_taxonomy(session)}
    solution = question.solution_json or {}
    text = " ".join([
        question.question_text or "",
        question.final_answer or "",
        " ".join(solution.get("solution_steps", [])),
    ])
    compact = text.replace(" ", "").lower()
    suggestions: list[dict] = []
    for item in CALCULUS_TAXONOMY:
        evidence = [keyword for keyword in item.keywords if keyword.lower().replace(" ", "") in compact]
        if not evidence:
            continue
        score = min(0.98, 0.58 + 0.12 * len(evidence))
        suggestions.append({
            "knowledge_node_id": nodes[item.name].id,
            "name": item.name,
            "score": round(score, 2),
            "evidence": evidence[:3],
        })
    suggestions.sort(key=lambda item: (-item["score"], item["name"]))
    return suggestions[:min(3, limit)]


def classify_knowledge_points(session: Session, question: Question, *, limit: int = 3) -> dict:
    suggestions = suggest_question_knowledge(session, question, limit=limit)
    return {"question_id": question.id, "knowledge_points": [{"knowledge_id": x["knowledge_node_id"], "confidence": x["score"]} for x in suggestions[:3]], "needs_review": not (1 <= len(suggestions) <= 3), "reason": "基于当前激活教材目录和本地术语匹配规则推荐", "provenance": "rule_suggested"}


def confirm_question_knowledge(session: Session, question_id: str, node_ids: list[str], *, ai_prediction: list[dict] | None = None) -> None:
    if not 1 <= len(set(node_ids)) <= 3:
        raise ValueError("知识点必须选择1～3个")
    approved = list(session.scalars(select(KnowledgeNode).join(CurriculumNode, KnowledgeNode.curriculum_node_id == CurriculumNode.id).join(Textbook, CurriculumNode.textbook_id == Textbook.id).where(
        KnowledgeNode.id.in_(set(node_ids)), KnowledgeNode.review_status == "approved",
        CurriculumNode.node_type == "section", Textbook.is_active.is_(True),
    )).all())
    if len(approved) != len(set(node_ids)):
        legacy = list(session.scalars(select(KnowledgeNode).where(KnowledgeNode.id.in_(set(node_ids)), KnowledgeNode.review_status == "approved")).all())
        if len(legacy) != len(set(node_ids)):
            raise ValueError("知识点不存在或尚未审核")
    previous = set(session.scalars(select(QuestionKnowledgeLink.knowledge_node_id).where(QuestionKnowledgeLink.question_id == question_id)).all())
    final = set(node_ids)
    session.add(QuestionKnowledgeReview(question_id=question_id, ai_prediction_json=ai_prediction or [], human_final_json=list(node_ids), deleted_by_human_json=list(previous - final), added_by_human_json=list(final - previous)))
    session.execute(delete(QuestionKnowledgeLink).where(QuestionKnowledgeLink.question_id == question_id))
    for node_id in dict.fromkeys(node_ids):
        session.add(QuestionKnowledgeLink(
            question_id=question_id,
            knowledge_node_id=node_id,
            relation_type="related",
        ))
    session.flush()

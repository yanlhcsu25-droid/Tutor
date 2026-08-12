import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from calculus_agent.config import get_settings
from calculus_agent.db import create_schema, session_scope
from calculus_agent.models import KnowledgeNode, Question, QuestionDraft, QuestionKnowledgeLink

SOURCE = "built-in-demo"

QUESTIONS = [
    ("选择题", "一次函数", "函数 y=2x+1 的斜率是（  ）\nA. 1\nB. 2\nC. -1\nD. -2", "B", "斜率是x的系数2。"),
    ("选择题", "一次函数", "点（1，3）在下列哪个函数图象上（  ）\nA. y=x+1\nB. y=2x+1\nC. y=3x+1\nD. y=2x-1", "B", "把x=1代入，y=3。"),
    ("选择题", "整式运算", "计算 2x+3x 的结果是（  ）\nA. 5\nB. 5x\nC. 6x\nD. x", "B", "合并同类项得5x。"),
    ("选择题", "几何", "三角形内角和为（  ）\nA. 90°\nB. 120°\nC. 180°\nD. 360°", "C", "三角形内角和为180°。"),
    ("选择题", "方程", "方程 x+2=5 的解为（  ）\nA. 2\nB. 3\nC. 5\nD. 7", "B", "移项得x=3。"),
    ("选择题", "一次函数", "一次函数 y=-x+4 的图象随x增大而（  ）\nA. 上升\nB. 下降\nC. 不变\nD. 无法判断", "B", "斜率为负，函数递减。"),
    ("填空题", "一次函数", "一次函数 y=3x-2 中，当x=2时，y=______。", "4", "代入得y=6-2=4。"),
    ("填空题", "方程", "方程 2x=10 的解是x=______。", "5", "两边同除以2。"),
    ("填空题", "整式运算", "计算 x·x 的结果为______。", "x²", "同底数幂相乘，指数相加。"),
    ("填空题", "几何", "直角三角形的两个锐角之和为______度。", "90", "三角形内角和减去直角。"),
    ("解答题", "一次函数", "已知一次函数 y=2x+1，分别求x=0和x=3时的函数值。", "1，7", "分别代入x=0和x=3，得到y=1和y=7。"),
    ("解答题", "一次函数", "已知直线经过点（0，1）和（2，5），求这条直线的函数表达式。", "y=2x+1", "斜率为(5-1)/(2-0)=2，截距为1。"),
    ("解答题", "方程", "解方程 3(x-1)=12，并写出检验过程。", "x=5", "展开得3x-3=12，所以3x=15，x=5。"),
    ("解答题", "几何", "一个三角形的两个内角分别为45°和65°，求第三个内角。", "70°", "第三个角为180°-45°-65°=70°。"),
    ("解答题", "一次函数", "某出租车收费由起步价和里程费组成。行驶2千米收费11元，行驶5千米收费17元，求收费y与里程x之间的一次函数关系。", "y=2x+7", "斜率为(17-11)/(5-2)=2，再代入求得截距7。"),
]


def seed_demo_questions(session: Session) -> tuple[int, int]:
    created = existing = 0
    knowledge_by_name: dict[str, KnowledgeNode] = {}
    for index, (question_type, knowledge_name, text, answer, solution) in enumerate(
        QUESTIONS, start=1
    ):
        found = session.scalar(
            select(QuestionDraft).where(
                QuestionDraft.source_name == SOURCE,
                QuestionDraft.source_item_id == str(index),
                QuestionDraft.variant == 1,
            )
        )
        if found:
            existing += 1
            continue
        node = knowledge_by_name.get(knowledge_name)
        if node is None:
            node = session.scalar(
                select(KnowledgeNode).where(
                    KnowledgeNode.node_type == "concept",
                    KnowledgeNode.normalized_name == knowledge_name,
                )
            )
        if node is None:
            node = KnowledgeNode(
                node_type="concept",
                name=knowledge_name,
                normalized_name=knowledge_name,
                source_type="built_in_demo",
                review_status="approved",
            )
            session.add(node)
            session.flush()
        knowledge_by_name[knowledge_name] = node
        draft = QuestionDraft(
            source_name=SOURCE,
            source_item_id=str(index),
            variant=1,
            subject="初中数学",
            grade="八年级",
            question_type=question_type,
            question_text=text,
            reference_answers_json=[answer],
            solution_text=solution,
            normalized_fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            status="approved",
        )
        session.add(draft)
        session.flush()
        question = Question(
            draft_id=draft.id,
            question_text=text,
            grade="八年级",
            question_type=question_type,
            final_answer=answer,
            solution_json={"solution_steps": [solution]},
            verification_status="built_in_reference",
            review_status="approved",
        )
        session.add(question)
        session.flush()
        session.add(
            QuestionKnowledgeLink(
                question_id=question.id,
                knowledge_node_id=node.id,
                relation_type="primary_concept",
                evidence_json=["内置演示题库"],
            )
        )
        created += 1
    session.flush()
    return created, existing


def main() -> None:
    settings = get_settings()
    create_schema(settings.database_url)
    with session_scope(settings.database_url) as session:
        created, existing = seed_demo_questions(session)
    print(f"演示题库准备完成：新增 {created} 题，已有 {existing} 题")


if __name__ == "__main__":
    main()

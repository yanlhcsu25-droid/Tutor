import re
from collections import Counter

from sqlalchemy import or_, select

from calculus_agent.knowledge.retrieval import retrieve_knowledge
from calculus_agent.models import KnowledgeNode, Question, QuestionDraft, QuestionKnowledgeLink
from calculus_agent.orchestration.types import AgentRunContext, AgentTool
from calculus_agent.papers.selector import compose_paper
from calculus_agent.schemas import PaperBlueprint


def knowledge_tools(context: AgentRunContext) -> list[AgentTool]:
    def search(arguments: dict) -> dict:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        matches = retrieve_knowledge(
            context.session,
            query,
            node_type=arguments.get("node_type"),
            limit=int(arguments.get("limit") or 10),
        )
        return {
            "matches": [
                {
                    "id": item.node.id,
                    "name": item.node.name,
                    "node_type": item.node.node_type,
                    "score": item.score,
                    "reasons": item.reasons,
                }
                for item in matches
            ]
        }

    def inspect_supply(arguments: dict) -> dict:
        statement = select(Question).where(
            Question.review_status == "approved",
            Question.is_active.is_(True),
            Question.knowledge_match_status == "current",
        )
        questions = list(context.session.scalars(statement).all())
        requested_knowledge = {
            str(item).strip() for item in arguments.get("knowledge_names", []) if str(item).strip()
        }
        knowledge_counts: Counter[str] = Counter()
        accepted = []
        with_images = 0
        for question in questions:
            names = set(
                context.session.scalars(
                    select(KnowledgeNode.name)
                    .join(
                        QuestionKnowledgeLink,
                        QuestionKnowledgeLink.knowledge_node_id == KnowledgeNode.id,
                    )
                    .where(QuestionKnowledgeLink.question_id == question.id)
                ).all()
            )
            if requested_knowledge and not requested_knowledge.intersection(names):
                continue
            accepted.append(question)
            draft = context.session.get(QuestionDraft, question.draft_id)
            with_images += bool(draft and draft.image_path)
            knowledge_counts.update(names)
        return {
            "total": len(accepted),
            "by_question_type": dict(Counter(item.question_type for item in accepted)),
            "by_knowledge": dict(knowledge_counts.most_common(20)),
            "with_images": with_images,
        }

    return [
        AgentTool(
            name="search_knowledge",
            description="检索已审核的规范知识点，返回节点ID、名称和匹配依据。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "node_type": {
                        "type": "string",
                        "enum": ["concept", "problem_type", "method"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
            handler=search,
        ),
        AgentTool(
            name="inspect_question_supply",
            description="统计知识点条件下的题库供给，不生成试卷。",
            parameters={
                "type": "object",
                "properties": {
                    "knowledge_names": {"type": "array", "items": {"type": "string"}},
                },
            },
            handler=inspect_supply,
        ),
    ]


def paper_tools(context: AgentRunContext) -> list[AgentTool]:
    def compose(arguments: dict) -> dict:
        raw = arguments.get("blueprint") or arguments
        blueprint = PaperBlueprint.model_validate(raw)
        paper = compose_paper(context.session, blueprint)
        context.current_paper = paper
        return paper.model_dump(mode="json")

    return [
        AgentTool(
            name="compose_paper",
            description=(
                "按结构化硬约束调用确定性算法组卷。不得擅自修改教师给出的题量、总分、"
                "题型数量或知识点配额。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "blueprint": PaperBlueprint.model_json_schema(),
                },
                "required": ["blueprint"],
            },
            handler=compose,
        )
    ]


def review_tools(context: AgentRunContext) -> list[AgentTool]:
    def validate(_: dict) -> dict:
        paper = context.current_paper
        if paper is None:
            raise ValueError("No current paper; compose a paper before review")
        issues = [
            {
                "code": "CONSTRAINT_UNSATISFIED",
                "severity": "error",
                "message": warning,
            }
            for warning in paper.warnings
        ]
        fingerprints: dict[str, list[str]] = {}
        for item in paper.items:
            fingerprint = re.sub(r"\s+|[，。；：、,.!?？！]", "", item.question_text).lower()
            fingerprints.setdefault(fingerprint, []).append(item.question_id)
            if not item.final_answer:
                issues.append(
                    {
                        "code": "MISSING_ANSWER",
                        "severity": "error",
                        "question_ids": [item.question_id],
                        "message": "题目缺少最终答案",
                    }
                )
            if not item.solution_steps:
                issues.append(
                    {
                        "code": "MISSING_SOLUTION",
                        "severity": "error",
                        "question_ids": [item.question_id],
                        "message": "题目缺少答案解析",
                    }
                )
            broken_commands = sorted(
                set(
                    re.findall(
                        r"(?<!\\)\b(?:frac|times|therefore|because|cdot|neq|geqslant|leqslant)\b",
                        item.question_text + "\n" + "\n".join(item.solution_steps),
                    )
                )
            )
            if broken_commands:
                issues.append(
                    {
                        "code": "BROKEN_LATEX",
                        "severity": "error",
                        "question_ids": [item.question_id],
                        "message": "疑似损坏的 LaTeX 命令：" + "、".join(broken_commands),
                    }
                )
        for ids in fingerprints.values():
            if len(ids) > 1:
                issues.append(
                    {
                        "code": "EXACT_DUPLICATE",
                        "severity": "error",
                        "question_ids": ids,
                        "message": "试卷中存在完全重复题目",
                    }
                )
        return {
            "status": "passed" if not issues else "needs_revision",
            "issues": issues,
            "summary": {
                "question_count": len(paper.items),
                "total_score": paper.total_score,
                "constraint_checks": [item.model_dump() for item in paper.constraints],
            },
        }

    return [
        AgentTool(
            name="validate_current_paper",
            description="审核当前试卷的硬约束、图片要求、答案解析、公式完整性和完全重复题。",
            parameters={"type": "object", "properties": {}},
            handler=validate,
        )
    ]

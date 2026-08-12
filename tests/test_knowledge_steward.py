from calculus_agent.knowledge.curriculum import import_curriculum
from calculus_agent.knowledge.normalization import normalize_name
from calculus_agent.knowledge.retrieval import retrieve_knowledge
from calculus_agent.knowledge.steward import process_draft
from calculus_agent.models import KnowledgeNode, QuestionDraft
from calculus_agent.questions.review import approve_draft
from calculus_agent.schemas import DraftApproveRequest
from calculus_agent.solver.service import ReferenceAnswerSolver


def test_processes_and_publishes_only_after_teacher_approval(session) -> None:
    import_curriculum(
        session,
        """第一章 函数与极限
第一节 函数的极限
两个重要极限""",
    )
    method = KnowledgeNode(
        node_type="method",
        name="重要极限",
        normalized_name=normalize_name("重要极限"),
        source_type="teacher",
        confidence=1.0,
        review_status="approved",
    )
    problem_type = KnowledgeNode(
        node_type="problem_type",
        name="三角函数型极限",
        normalized_name=normalize_name("三角函数型极限"),
        source_type="teacher",
        confidence=1.0,
        review_status="approved",
    )
    session.add_all([method, problem_type])
    session.flush()
    draft = QuestionDraft(
        source_name="test",
        source_item_id="q-1",
        variant=1,
        subject="Calculus_-_single_variable",
        source_topic="函数的极限",
        source_subtopic="两个重要极限",
        question_text=r"求极限 $\lim_{x\to0}\frac{\sin x}{x}$",
        reference_answers_json=["1"],
        answer_types_json=["NV"],
        options_json=[[]],
        level="1",
        keywords_json=["极限", "三角函数"],
        normalized_fingerprint="f" * 64,
    )
    session.add(draft)
    session.flush()

    processed = process_draft(session, draft.id, ReferenceAnswerSolver("1"))
    assert processed.status == "ready_for_review"
    assert processed.verification.status == "verified"
    assert draft.status != "approved"

    concepts = [item for item in processed.candidates if item.node_type == "concept"]
    assert concepts
    published = approve_draft(
        session,
        draft.id,
        DraftApproveRequest(
            primary_concept_id=concepts[0].knowledge_node_id,
            problem_type_ids=[problem_type.id],
            method_ids=[method.id],
        ),
    )
    assert published.verification_status == "verified"
    assert {item.node_type for item in published.knowledge} == {
        "concept",
        "problem_type",
        "method",
    }
    assert draft.status == "approved"


def test_retrieval_uses_approved_nodes_only(session) -> None:
    approved = KnowledgeNode(
        node_type="method",
        name="洛必达法则",
        normalized_name=normalize_name("洛必达法则"),
        source_type="teacher",
        confidence=1,
        review_status="approved",
    )
    proposed = KnowledgeNode(
        node_type="method",
        name="洛必达公式",
        normalized_name=normalize_name("洛必达公式"),
        source_type="agent_candidate",
        confidence=0.6,
        review_status="proposed",
    )
    session.add_all([approved, proposed])
    session.flush()

    results = retrieve_knowledge(session, "使用洛必达法则求极限")
    assert [item.node.name for item in results] == ["洛必达法则"]

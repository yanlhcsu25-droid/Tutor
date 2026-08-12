"""回归测试：OCR 草稿发布到正式题库时，必须按结构化字段同步关系，不依赖 Markdown。

覆盖用户验收要求：
- knowledge_points_json 真正转换为平级 QuestionKnowledgeLink
- difficulty_level 写入 QuestionProfile（approved），使题库可展示难度
- 章节由全部知识点的教材目录层级反推并去重
- 不再从 OCR Markdown 解析知识点 / 难度
"""

from sqlalchemy import select

from calculus_agent.models import (
    CurriculumNode,
    KnowledgeNode,
    OcrImportDraft,
    OcrImportSource,
    Question,
    QuestionDraft,
    QuestionKnowledgeLink,
    QuestionProfile,
    Textbook,
)
from calculus_agent.ocr.import_service import publish_ocr_draft


def _seed(session):
    book = Textbook(name="高等数学", edition="测试版", is_active=True)
    session.add(book)
    session.flush()
    chapter = CurriculumNode(
        textbook_id=book.id, node_type="chapter",
        title="导数与微分", sort_order=10, review_status="approved",
    )
    session.add(chapter)
    session.flush()
    nodes = []
    for order, name in enumerate(
        ("隐函数及由参数方程所确定的函数的导数", "函数的求导法则", "高阶导数"), start=11
    ):
        curriculum = CurriculumNode(
            textbook_id=book.id, parent_id=chapter.id, node_type="section",
            title=name, sort_order=order, review_status="approved",
        )
        session.add(curriculum)
        session.flush()
        node = KnowledgeNode(
            curriculum_node_id=curriculum.id, node_type="concept", name=name,
            normalized_name=name, source_type="textbook_directory", review_status="approved",
        )
        session.add(node)
        session.flush()
        nodes.append(node)

    source = OcrImportSource(
        id="src_seed00000000000000000000000000a",
        original_name="s.pdf", stored_path="/tmp/s.pdf",
        sha256="s" * 64, page_count=1, processing_status="done",
    )
    session.add(source)
    session.flush()
    # 新版模板：不含 章节 / 知识点 / 难度 section
    markdown = (
        "## 题目内容\n\n求隐函数导数\n\n"
        "## 参考解答\n\n解：对方程两边求导\n\n"
        "## 题型\n\ncalculation\n\n"
        "## 来源页码\n\n12\n\n"
        "## 原始题号\n\n3(1)\n\n"
        "## 审核备注\n\n"
    )
    draft = OcrImportDraft(
        id="q_seed00000000000000000000000000aa",
        source_id=source.id,
        page_number=12,
        original_number="3(1)",
        ocr_markdown=markdown,
        edited_markdown=markdown,
        review_status="reviewed",
        knowledge_points_json=[node.id for node in nodes],
        difficulty_level=2,
    )
    session.add(draft)
    session.flush()
    return draft, nodes


def test_publish_syncs_knowledge_links_and_profile(session):
    draft, nodes = _seed(session)
    result = publish_ocr_draft(session, draft)
    assert result is not None
    question = session.get(Question, result["question_id"])
    assert question is not None

    # 1) QuestionKnowledgeLink 来自结构化 knowledge_points_json（非 Markdown）
    links = list(session.scalars(
        select(QuestionKnowledgeLink)
        .where(QuestionKnowledgeLink.question_id == question.id)
        .order_by(QuestionKnowledgeLink.relation_type)
    ).all())
    assert [link.knowledge_node_id for link in links] == [node.id for node in nodes]
    assert {link.relation_type for link in links} == {"related"}

    # 2) QuestionProfile 来自结构化 difficulty_level，状态 approved 以便题库展示
    profile = session.scalar(
        select(QuestionProfile).where(QuestionProfile.question_id == question.id)
    )
    assert profile is not None
    assert profile.difficulty == 2
    assert profile.profile_status == "approved"
    # OCR 审核阶段难度为教师人工设定并确认 → human 来源（非 ocr_publish）
    assert profile.profile_source == "human"

    # 3) 同章不同知识点去重为一个章节
    draft_row = session.get(QuestionDraft, question.draft_id)
    assert draft_row.source_topic == "导数与微分"


def test_publish_without_knowledge_is_safe(session):
    draft, _ = _seed(session)
    # 清空结构化知识点，验证不报错、章节回退为 None（无 Markdown 章节可读）
    draft.knowledge_points_json = []
    draft.difficulty_level = 4
    result = publish_ocr_draft(session, draft)
    assert result is not None
    question = session.get(Question, result["question_id"])
    links = list(session.scalars(
        select(QuestionKnowledgeLink).where(QuestionKnowledgeLink.question_id == question.id)
    ).all())
    assert links == []
    profile = session.scalar(
        select(QuestionProfile).where(QuestionProfile.question_id == question.id)
    )
    assert profile.difficulty == 4
    assert profile.profile_status == "approved"
    assert profile.profile_source == "human"


def test_chapter_derivation_keeps_cross_chapter_and_ignores_order(session):
    draft, nodes = _seed(session)
    first_section = session.get(KnowledgeNode, nodes[0].id).curriculum_node_id
    section = session.get(CurriculumNode, first_section)
    chapter = CurriculumNode(
        textbook_id=section.textbook_id, node_type="chapter", title="积分学",
        sort_order=20, review_status="approved",
    )
    session.add(chapter)
    session.flush()
    other_section = CurriculumNode(
        textbook_id=chapter.textbook_id, parent_id=chapter.id, node_type="section",
        title="定积分", sort_order=21, review_status="approved",
    )
    session.add(other_section)
    session.flush()
    other_node = KnowledgeNode(
        curriculum_node_id=other_section.id, node_type="concept", name="定积分",
        normalized_name="定积分", source_type="textbook_directory", review_status="approved",
    )
    session.add(other_node)
    session.flush()

    from calculus_agent.ocr.import_service import derive_chapter_from_knowledge
    first = derive_chapter_from_knowledge(session, [nodes[0].id, other_node.id])
    second = derive_chapter_from_knowledge(session, [other_node.id, nodes[0].id])
    assert first == second
    assert first == "导数与微分；积分学"


def test_revision_republish_overwrites_profile_with_human(session):
    """revision 再发布经 publish_ocr_draft 的 formal_question_id 分支，
    覆盖式重写同一正式题的 QuestionProfile，必须生成合法 profile_source=human，
    不能产生 ocr_publish 等非法值。"""
    draft, nodes = _seed(session)
    first = publish_ocr_draft(session, draft)
    assert first is not None
    question = session.get(Question, first["question_id"])

    # 模拟 create_revision 产出的修订草稿（保留 formal_question_id，沿用结构化字段）
    revision = OcrImportDraft(
        id="q_rev00000000000000000000000000bb",
        source_id=draft.source_id,
        page_number=draft.page_number,
        original_number=draft.original_number,
        ocr_markdown=draft.ocr_markdown,
        edited_markdown=draft.edited_markdown,
        review_status="in_review",
        revision_of_id=draft.id,
        formal_question_id=question.id,
        knowledge_points_json=list(draft.knowledge_points_json or []),
        difficulty_level=draft.difficulty_level,
    )
    session.add(revision)
    session.flush()

    second = publish_ocr_draft(session, revision)
    assert second is not None
    # 修订再发布更新同一正式题，不新建
    assert second["question_id"] == question.id

    profiles = list(session.scalars(
        select(QuestionProfile).where(QuestionProfile.question_id == question.id)
    ).all())
    # 覆盖式同步：仅一条，且为 human
    assert len(profiles) == 1
    assert profiles[0].profile_source == "human"
    assert profiles[0].difficulty == 2
    assert profiles[0].profile_status == "approved"

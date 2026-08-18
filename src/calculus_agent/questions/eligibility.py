"""Shared paper-question eligibility contract.

Question-bank inspection and the paper selector must reason over the same
candidate universe. Keep the base SQL predicate here so environment evidence
cannot report supply that generation would later reject.
"""

from __future__ import annotations

from sqlalchemy import select

from calculus_agent.models import Question, QuestionDraft


EXCLUDED_PAPER_SOURCE_NAMES = frozenset({
    "CMM-Math",
    "built-in-demo",
    "test_source",
})


def paper_candidate_statement():
    """Return the shared base statement for teacher-facing paper candidates."""
    return (
        select(Question)
        .join(
            QuestionDraft,
            QuestionDraft.id == Question.draft_id,
        )
        .where(
            Question.review_status == "approved",
            Question.is_active.is_(True),
            Question.knowledge_match_status == "current",
            QuestionDraft.source_name.not_in(
                EXCLUDED_PAPER_SOURCE_NAMES
            ),
        )
    )

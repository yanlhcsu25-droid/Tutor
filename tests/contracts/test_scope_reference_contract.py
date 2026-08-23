from tests.evals.curriculum_fixture import seed_eval_curriculum
from tests.evals.runner import create_eval_session

from calculus_agent.application.scope_resolution import resolve_deterministic_scope_labels


def test_explicit_chapter_ordinal_accepts_attached_topic_text():
    session = create_eval_session()
    try:
        seed_eval_curriculum(session)
        result = resolve_deterministic_scope_labels(session, ["第三章导数"])

        assert result.ok
        assert result.validated_scope_names == ["第三章 微分中值定理与导数的应用"]
    finally:
        session.close()

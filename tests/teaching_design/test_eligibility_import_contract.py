def test_eligibility_constant_has_one_canonical_value_and_legacy_reexport():
    from calculus_agent.questions.eligibility import (
        EXCLUDED_PAPER_SOURCE_NAMES as canonical,
    )
    from calculus_agent.papers.selector import (
        EXCLUDED_PAPER_SOURCE_NAMES as selector_compat,
    )
    from calculus_agent.agent.tools import replacement_tools

    assert selector_compat is canonical
    assert replacement_tools.EXCLUDED_PAPER_SOURCE_NAMES is canonical
    assert canonical == frozenset({
        "CMM-Math",
        "built-in-demo",
        "test_source",
    })


def test_replacement_tools_imports_after_t3_eligibility_refactor():
    from calculus_agent.agent.tools.replacement_tools import (
        dry_run_replace_question,
    )

    assert callable(dry_run_replace_question)

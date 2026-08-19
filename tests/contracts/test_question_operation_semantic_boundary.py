"""Regression tests for Teacher Agent semantic/deterministic boundary."""

from pathlib import Path

from calculus_agent.agent.agent import (
    _apply_explicit_opt_in_guards,
    _apply_question_reference_hints,
    _explicit_preserve_knowledge_points_requested,
    _explicit_question_addresses,
    _explicit_question_positions,
)


def _address_pairs(message: str) -> list[tuple[str, int]]:
    return [
        (address.section_type, address.section_order)
        for address in _explicit_question_addresses(message)
    ]


def test_canonical_section_address_is_positive_hint():
    assert _address_pairs("填空题第3题换一道") == [("填空题", 3)]


def test_reversed_natural_address_is_positive_hint():
    assert _address_pairs("第三题这道填空题超纲了，需要换一道") == [("填空题", 3)]


def test_bare_question_number_is_not_forced_to_global_position():
    assert _address_pairs("第三题换一下") == []
    assert _explicit_question_positions("第三题换一下") == []


def test_only_explicit_global_wording_creates_global_position():
    assert _explicit_question_positions("全卷第8题换一道") == [8]


def test_read_hint_fills_missing_address_only():
    addresses = _explicit_question_addresses("填空题第3题")
    args = _apply_question_reference_hints(
        tool_name="read_paper",
        arguments={},
        addresses=addresses,
        positions=[],
    )
    assert args == {"addresses": [{"section_type": "填空题", "section_order": 3}]}


def test_positive_hint_never_overwrites_model_target():
    addresses = _explicit_question_addresses("填空题第3题")
    args = _apply_question_reference_hints(
        tool_name="preview_paper_changes",
        arguments={
            "operations": [{
                "type": "replace_question",
                "target": {"section_type": "选择题", "section_order": 1},
                "difficulty_direction": "easier",
            }]
        },
        addresses=addresses,
        positions=[],
    )
    operation = args["operations"][0]
    assert operation["target"] == {"section_type": "选择题", "section_order": 1}
    assert operation["difficulty_direction"] == "easier"


def test_python_does_not_infer_remove_operation_from_keywords():
    addresses = _explicit_question_addresses("删除填空题第3题")
    original = {"target_total_score": 90}
    args = _apply_question_reference_hints(
        tool_name="preview_paper_changes",
        arguments=original,
        addresses=addresses,
        positions=[],
    )
    assert args == original
    assert "operations" not in args


def test_single_missing_change_target_can_use_positive_hint():
    addresses = _explicit_question_addresses("填空题第3题换一道")
    args = _apply_question_reference_hints(
        tool_name="preview_paper_changes",
        arguments={
            "operations": [{
                "type": "replace_question",
                "difficulty_direction": "easier",
            }]
        },
        addresses=addresses,
        positions=[],
    )
    assert args["operations"][0]["target"] == {"section_type": "填空题", "section_order": 3}


def test_preserve_knowledge_points_is_explicit_opt_in():
    assert _explicit_preserve_knowledge_points_requested("知识点别变，把填空题第3题换一道")
    assert not _explicit_preserve_knowledge_points_requested("填空题第3题超纲了，换一道")


def test_unrequested_preserve_constraint_is_downgraded():
    args = _apply_explicit_opt_in_guards(
        tool_name="preview_paper_changes",
        arguments={
            "operations": [{
                "type": "replace_question",
                "target": {"section_type": "填空题", "section_order": 3},
                "preserve_knowledge_points": True,
            }]
        },
        message="填空题第3题超纲了，换一道",
    )
    assert args["operations"][0]["preserve_knowledge_points"] is False


def test_explicit_preserve_constraint_is_kept():
    args = _apply_explicit_opt_in_guards(
        tool_name="preview_paper_changes",
        arguments={
            "operations": [{
                "type": "replace_question",
                "target": {"section_type": "填空题", "section_order": 3},
                "preserve_knowledge_points": True,
            }]
        },
        message="知识点别变，把填空题第3题换一道",
    )
    assert args["operations"][0]["preserve_knowledge_points"] is True


def test_agent_source_has_no_pre_llm_bare_number_semantic_veto():
    import calculus_agent.agent.agent as agent_module

    source = Path(agent_module.__file__).read_text(encoding="utf-8")
    assert "_has_ambiguous_bare_question_reference" not in source
    assert "ambiguous_bare_question" not in source
    assert 're.search(r"删除|删掉|去掉|移除", message)' not in source

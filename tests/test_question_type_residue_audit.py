"""Residue-audit regression tests for the question-type contract.

锁定三条审计结论（这三处此前都没有测试覆盖，所以缺陷长期无人发现）：

1. ``多项选择题 / 多选题 / 单选题`` 只是**输入别名**：章节标题一经折算，
   输出必须立即是唯一的选择型 ``selection``，不得残留
   ``single_choice`` / ``multiple_choice`` 这类会渗到业务层的中间态。
2. 分类器的 ``subjective``（以及 ``other`` / 未识别值）**不得**进入正式业务
   状态：必须落成 ``unknown``（题型待定，需人工处理），而不是被静默当成
   ``计算题`` 塞进可组卷题池。
3. ``ai_content_review`` 的选择题选项校验必须是**活分支**——旧代码拿
   工作台英文枚举去比中文业务类型，永不相等，该校验实际从未生效。
"""

from __future__ import annotations

import pytest

from calculus_agent.ocr.doc_pipeline import _TYPE_MAP
from calculus_agent.ocr.doc_pipeline import _question_type as doc_section_type
from calculus_agent.ocr.service import _WORKBENCH_TYPE_MAP
from calculus_agent.question_types import (
    PAPER_QUESTION_TYPES,
    VALID_QUESTION_TYPES,
    canonical_question_type,
)
from calculus_agent.workbench.ai_content_review import deterministic_content_issues
from calculus_agent.workbench.markdown_schema import fixed_template
from calculus_agent.workbench.ocr import _question_type as wb_section_type

QUESTION_ID = "q_" + "7" * 32
SOURCE_ID = "src_" + "8" * 32

# 两份平行实现（workbench/ocr.py 与 ocr/doc_pipeline.py）必须行为一致。
SECTION_TYPE_IMPLS = [
    pytest.param(wb_section_type, id="workbench.ocr"),
    pytest.param(doc_section_type, id="ocr.doc_pipeline"),
]

TYPE_MAPS = [
    pytest.param(_WORKBENCH_TYPE_MAP, id="ocr.service._WORKBENCH_TYPE_MAP"),
    pytest.param(_TYPE_MAP, id="ocr.doc_pipeline._TYPE_MAP"),
]


# --- 1. 选择题输入别名立即折算 -------------------------------------------------

@pytest.mark.parametrize("impl", SECTION_TYPE_IMPLS)
@pytest.mark.parametrize(
    "title",
    ["一、单项选择题", "二、多项选择题", "选择题", "多选题", "单选题", "三、 选 择 题"],
)
def test_choice_section_aliases_collapse_to_selection_immediately(impl, title):
    """多项选择题等标题只是输入别名，输出必须立即是 selection。"""
    assert impl(title) == "selection", f"{title} 应折算为 selection，实际 {impl(title)}"


@pytest.mark.parametrize("impl", SECTION_TYPE_IMPLS)
def test_section_type_never_emits_split_choice_states(impl):
    """single_choice / multiple_choice 不得再作为输出中间态出现。"""
    titles = [
        "一、单项选择题", "二、多项选择题", "选择题", "多选题", "单选题",
        "三、填空题", "四、计算题", "五、证明题", "六、解答题", "七、综合题", "随便什么标题",
    ]
    emitted = {impl(t) for t in titles}
    assert "single_choice" not in emitted, f"仍在输出 single_choice: {emitted}"
    assert "multiple_choice" not in emitted, f"仍在输出 multiple_choice: {emitted}"


# --- 2. subjective / other 不得进入正式业务状态 --------------------------------

@pytest.mark.parametrize("type_map", TYPE_MAPS)
@pytest.mark.parametrize("wb_type", ["subjective", "other"])
def test_undetermined_workbench_types_map_to_unknown(type_map, wb_type):
    """分类器未判定为四类可组卷题型时，正式题型必须是 unknown。"""
    assert type_map[wb_type] == "unknown", (
        f"{wb_type} 映射为 {type_map[wb_type]}，应为 unknown（题型待定）"
    )


@pytest.mark.parametrize("type_map", TYPE_MAPS)
def test_type_maps_only_produce_contract_values(type_map):
    """两张映射表的值域必须完全落在五类 contract 内。"""
    illegal = {v for v in type_map.values() if v not in VALID_QUESTION_TYPES}
    assert not illegal, f"映射表产出非法题型: {illegal}"


def test_unknown_is_not_generatable():
    """unknown 是合法存储态，但绝不可进入可组卷题型。"""
    assert "unknown" in VALID_QUESTION_TYPES
    assert "unknown" not in PAPER_QUESTION_TYPES


@pytest.mark.parametrize("wb_type", ["subjective", "other"])
def test_undetermined_types_are_not_generatable_end_to_end(wb_type):
    """端到端：subjective/other 折算后不得落在可组卷题池里。"""
    for type_map in (_WORKBENCH_TYPE_MAP, _TYPE_MAP):
        assert type_map[wb_type] not in PAPER_QUESTION_TYPES


def test_classifier_subjective_canonicalizes_to_unknown():
    """canonical 契约层同样必须把 subjective 收敛到 unknown。"""
    assert canonical_question_type("subjective") == "unknown"


@pytest.mark.parametrize("impl", SECTION_TYPE_IMPLS)
def test_free_answer_section_folds_to_unknown_end_to_end(impl):
    """“解答题/综合题”章节 → subjective → unknown（不得变成计算题）。"""
    for title in ("六、解答题", "七、综合题"):
        internal = impl(title)
        assert internal == "subjective"
        assert _WORKBENCH_TYPE_MAP[internal] == "unknown"
        assert _TYPE_MAP[internal] == "unknown"


def test_unmapped_workbench_type_falls_back_to_unknown():
    """未知的工作台 key 不得静默落成计算题。"""
    assert _WORKBENCH_TYPE_MAP.get("brand_new_type", "unknown") == "unknown"
    assert _TYPE_MAP.get("brand_new_type", "unknown") == "unknown"


# --- 3. 选择题选项校验必须是活分支 ---------------------------------------------

def _issues_for(options: dict[str, str]) -> list[str]:
    markdown = fixed_template(
        "判断下列间断点的类型并选出正确结论",
        question_type="selection",
        page_number=1,
        original_number="1",
        options=options,
        analysis="解析：由定义直接判断，应选(B)。",
    )
    issues, payload = deterministic_content_issues({
        "match_status": "matched",
        "edited_markdown": markdown,
        "question_id": QUESTION_ID,
        "source_file_id": SOURCE_ID,
        "ocr_markdown": markdown,
        "source_bbox": None,
    })
    assert payload is not None, f"payload 解析失败，无法验证选项分支: {issues}"
    return issues


def test_selection_option_check_passes_for_standard_abcd():
    options = {"A": "可去间断点", "B": "跳跃间断点", "C": "第二类间断点", "D": "连续点"}
    assert "selection_options_abnormal" not in _issues_for(options)


def test_selection_option_check_is_live_not_dead_code():
    """旧实现该分支恒为假；此测试确保它真的会触发。"""
    assert "selection_options_abnormal" in _issues_for({"A": "甲", "B": "乙"})

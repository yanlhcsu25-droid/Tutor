import pytest

from calculus_agent.workbench.markdown_schema import render_preview
from calculus_agent.workbench.ocr import PlacedCandidate, normalize_page, render_drafts, split_page_markdown


@pytest.mark.parametrize("blank", ["____", "_____", "＿＿＿＿", "————", "────"])
def test_explicit_fill_blank_marks_survive_clean_split_and_render(blank):
    source = f"1. 填空：函数 f(x)={blank} 时连续。"
    normalized = normalize_page(source)
    assert blank in normalized
    candidate = split_page_markdown(source)[0]
    assert blank in candidate.body
    draft = render_drafts(PlacedCandidate(1, candidate))[0]
    assert blank in draft.markdown
    rendered_html, issues = render_preview(draft.markdown)
    assert not issues
    assert blank in rendered_html


def test_pipeline_does_not_invent_blank_when_raw_ocr_has_none():
    source = "1. 已知函数在 x=0 连续，则 a="
    candidate = split_page_markdown(source)[0]
    draft = render_drafts(PlacedCandidate(1, candidate))[0]
    assert candidate.body.endswith("a=")
    assert "____" not in draft.markdown


def test_escaped_ocr_blank_is_normalized_for_storage_and_preview():
    source = r"1. 若向量满足条件，则 $r =$ \_\_\_\_."

    normalized = normalize_page(source)
    candidate = split_page_markdown(source)[0]
    draft = render_drafts(PlacedCandidate(1, candidate))[0]
    rendered_html, issues = render_preview(draft.markdown)

    assert normalized.endswith("$r =$ ____.")
    assert r"\_" not in candidate.body
    assert "____" in draft.markdown
    assert r"\_" not in rendered_html
    assert "____" in rendered_html
    assert not issues

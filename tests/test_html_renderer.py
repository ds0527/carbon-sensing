"""HTML·PDF 렌더러: 듀오톤 템플릿, 카테고리 그룹, 1장 맞춤."""

from __future__ import annotations

import pytest

from carbon_sensing.report.html_renderer import (
    CATEGORY_CHIP,
    render_report_html,
    render_summary_html,
)
from tests.test_summary import _result


def test_summary_html_has_hero_and_headline():
    html = render_summary_html(_result())
    assert '<div class="hero">' in html
    assert "철강 탄소중립 전환이 가속됐다" in html


def test_summary_html_groups_by_category():
    html = render_summary_html(_result())
    for category in ("정책", "설비", "기술"):
        assert f">{category}</span>" in html


def test_category_chips_are_visually_distinct():
    """듀오톤 안에서 카테고리를 구분하려면 칩 스타일이 서로 달라야 한다."""
    styles = [CATEGORY_CHIP[c] for c in ("정책", "설비", "기술", "시장")]
    assert len(set(styles)) == len(styles), f"칩 스타일 중복: {styles}"


def test_summary_html_embeds_css():
    html = render_summary_html(_result())
    assert "--duo-500" in html, "듀오톤 토큰이 들어가야 한다"
    assert "@media print" in html, "인쇄 변형이 들어가야 한다"


def test_hero_sets_width_before_aspect_ratio():
    """aspect-ratio만 주고 max-height를 걸면 폭이 줄어든다(실제로 겪은 버그)."""
    html = render_summary_html(_result())
    hero_css = html.split(".hero {")[1].split("}")[0]
    assert "width: 100%" in hero_css
    assert "aspect-ratio: 16 / 9" in hero_css


def test_summary_html_escapes_titles():
    result = _result()
    result.representative[0].title = "<script>alert(1)</script>"
    html = render_summary_html(result)
    assert "<script>alert(1)</script>" not in html


def test_report_html_has_document_table():
    html = render_report_html(_result())
    assert "관련도 순 문서 목록" in html
    assert "<table>" in html


def test_report_html_includes_execution_context():
    html = render_report_html(_result())
    assert "실행 조건" in html
    assert "CBAM" in html


def test_report_html_shows_skip_reason_without_llm():
    result = _result()
    result.representative = []
    result.insights = {}
    result.synthesis = None
    result.llm_skipped_reason = "LLM 단계를 건너뛰었습니다"
    html = render_report_html(result)
    assert "건너뛰었습니다" in html


def test_citation_numbers_present():
    html = render_summary_html(_result())
    assert 'class="cite"' in html


@pytest.mark.parametrize("renderer", [render_summary_html, render_report_html])
def test_renderers_survive_empty_result(renderer):
    result = _result(n_reps=0)
    result.representative = []
    result.insights = {}
    result.synthesis = None
    html = renderer(result)
    assert "<html" in html

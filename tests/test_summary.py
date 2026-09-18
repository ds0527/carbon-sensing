"""1장 summary: 분량 제한, 카테고리 그룹핑, report.md와 별개 생성."""

from __future__ import annotations

from carbon_sensing.daterange import build_range
from carbon_sensing.llm.service import DocumentInsight, GroundedText, Synthesis
from carbon_sensing.models import CollectorResult, RunContext
from carbon_sensing.report import RunResult, render_report, render_summary_one_page
from carbon_sensing.report.summary_renderer import MAX_CHARS
from tests.conftest import make_doc


def _result(n_reps: int = 5, long_text: bool = False):
    period = build_range("2026-09-01", "2026-09-15")
    categories = ["정책", "설비", "기술", "시장", "정책"]
    docs = []
    for i in range(n_reps):
        doc = make_doc(
            title=f"철강 탄소중립 기사 {i}",
            url=f"https://www.yna.co.kr/view/{i}",
        )
        doc.category = categories[i % len(categories)]
        doc.score = 60.0 - i
        docs.append(doc)

    filler = "설명이 아주 길어지는 문장입니다. " * (12 if long_text else 1)
    insights = {
        d.id: DocumentInsight(
            doc_id=d.id,
            summary=[GroundedText(text=f"{filler}요약 {d.title}", source_doc_id=d.id)],
            implications=[GroundedText(text=f"{filler}시사점", source_doc_id=d.id)],
            selection_reason="핵심 사안",
        )
        for d in docs
    }
    anchor = docs[0].id if docs else None
    synthesis = (
        Synthesis(
            headline="철강 탄소중립 전환이 가속됐다",
            trends=[
                GroundedText(text=f"[정책] {filler}트렌드 {i}", source_doc_id=anchor)
                for i in range(5)
            ],
            implications=[
                GroundedText(text=f"[사업] {filler}시사점 {i}", source_doc_id=anchor)
                for i in range(3)
            ],
        )
        if docs
        else None
    )
    ctx = RunContext(
        date_from=period.start,
        date_to=period.end,
        base_topic="철강산업의 탄소중립",
        extra_topics=["CBAM"],
        mode="and",
        excludes=[],
        queries=["철강 탄소중립"],
        top_n=n_reps,
    )
    return RunResult(
        context=ctx,
        period=period,
        documents=docs,
        collector_results=[CollectorResult(collector="test", documents=docs)],
        stats={"raw": 100, "in_period": 50, "by_category": {"정책": 2, "설비": 1}},
        representative=docs,
        insights=insights,
        synthesis=synthesis,
        usage={"total_tokens": 1000, "estimated_cost_usd": 0.01, "calls": 4},
    )


def test_summary_fits_one_page():
    text, meta = render_summary_one_page(_result())
    assert meta["fits_one_page"] is True
    assert len(text) <= MAX_CHARS


def test_long_content_is_compressed_to_fit():
    """대표 10건 + 긴 문장이면 문장 길이 제한을 낮춰 1장에 맞춘다."""
    text, meta = render_summary_one_page(_result(n_reps=10, long_text=True))
    assert len(text) <= MAX_CHARS
    assert meta["trend_limit"] < 110, "압축이 걸리지 않았다"


def test_representative_count_is_not_reduced_when_compressing():
    result = _result(n_reps=10, long_text=True)
    text, _ = render_summary_one_page(result)
    for doc in result.representative:
        assert doc.url in text, "분량 압축 시 대표 기사 건수는 줄이지 않는다"


def test_summary_groups_by_category():
    text, _ = render_summary_one_page(_result())
    assert "**[정책]**" in text
    assert "**[설비]**" in text
    assert "**[기술]**" in text


def test_summary_has_citation_numbers():
    text, _ = render_summary_one_page(_result())
    assert "[1]" in text


def test_report_and_summary_are_separate_documents():
    """1장 summary는 report.md를 대체하지 않는다."""
    result = _result()
    summary, _ = render_summary_one_page(result)
    report = render_report(result)
    assert len(report) > len(summary)
    # 상세 리포트에만 있는 섹션
    assert "## 2. 수집 현황" in report
    assert "## 2. 수집 현황" not in summary
    assert "부록" in report and "부록" not in summary


def test_report_groups_representative_by_category():
    report = render_report(_result())
    assert "### [정책]" in report
    assert "### [설비]" in report


def test_report_includes_category_column():
    report = render_report(_result())
    assert "| 순위 | 점수 | 카테고리 |" in report


def test_summary_without_llm_shows_reason():
    result = _result()
    result.representative = []
    result.insights = {}
    result.synthesis = None
    result.llm_skipped_reason = "LLM 단계를 건너뛰었습니다"
    text, _ = render_summary_one_page(result)
    assert "건너뛰었습니다" in text

"""report.md와 raw.json 렌더러. 2단계에서 요약 섹션이 얹힌다."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import PROJECT_DIR, get_app_config
from ..daterange import KST, DateRange
from ..models import CollectorResult, Document, RunContext

OUTPUT_ROOT = PROJECT_DIR / "outputs"


@dataclass
class RunResult:
    """파이프라인 한 번의 결과 전체."""

    context: RunContext
    period: DateRange
    documents: list[Document]
    collector_results: list[CollectorResult]
    date_unknown: list[Document] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    # 2단계 산출물. LLM을 돌리지 않았으면 비어 있다.
    representative: list[Document] = field(default_factory=list)
    insights: dict[str, Any] = field(default_factory=dict)
    synthesis: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    llm_skipped_reason: str | None = None

    @property
    def visible(self) -> list[Document]:
        threshold = get_app_config().scoring.min_score_to_show
        return [d for d in self.documents if (d.score or 0) >= threshold]

    @property
    def hidden(self) -> list[Document]:
        threshold = get_app_config().scoring.min_score_to_show
        return [d for d in self.documents if (d.score or 0) < threshold]

    def by_category(self) -> dict[str, list[Document]]:
        """카테고리 -> 대표 기사. 정책·설비·기술·시장·기타 순으로 돈다."""
        from ..processing.categorize import CATEGORIES, DEFAULT_CATEGORY

        grouped: dict[str, list[Document]] = {}
        for category in CATEGORIES + [DEFAULT_CATEGORY]:
            members = [d for d in self.representative if d.category == category]
            if members:
                grouped[category] = members
        return grouped

    def citation_map(self) -> dict[str, int]:
        """문서 id -> 각주 번호. 대표 기사 순서대로 1부터."""
        return {doc.id: i for i, doc in enumerate(self.representative, start=1)}


def make_output_dir(started_at: datetime | None = None) -> Path:
    moment = (started_at or datetime.now(KST)).astimezone(KST)
    path = OUTPUT_ROOT / moment.strftime("%Y-%m-%d_%H%M")
    suffix = 1
    base = path
    while path.exists():
        suffix += 1
        path = base.with_name(f"{base.name}-{suffix}")
    path.mkdir(parents=True)
    return path


def _md_cell(text: str) -> str:
    """파이프·줄바꿈이 섞여도 표가 깨지지 않게 이스케이프한다."""
    cleaned = re.sub(r"\s+", " ", (text or "").replace("|", r"\|")).strip()
    return cleaned or "-"


def _fmt_date(doc: Document) -> str:
    if doc.published_at is None:
        return "일자미확인"
    return doc.published_at.astimezone(KST).strftime("%Y-%m-%d")


def _doc_table(documents: list[Document], start_rank: int = 1) -> list[str]:
    lines = [
        "| 순위 | 점수 | 카테고리 | 제목 | 출처·유형 | 게재일 | 중복보도 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, doc in enumerate(documents, start=start_rank):
        title_cell = f"[{_md_cell(doc.title)}]({doc.url})"
        source_cell = _md_cell(f"{doc.source_name} · {doc.doc_type} · {doc.region}")
        lines.append(
            f"| {i} | {doc.score:.1f} | {doc.category} | {title_cell} | {source_cell} "
            f"| {_fmt_date(doc)} | {doc.duplicate_count} |"
        )
    return lines


def _claim_lines(claims: list[Any], citations: dict[str, int]) -> list[str]:
    """근거 문장을 각주 번호와 함께 렌더링한다."""
    out: list[str] = []
    for claim in claims:
        marker = ""
        if claim.source_doc_id and claim.source_doc_id in citations:
            marker = f" [{citations[claim.source_doc_id]}]"
        out.append(f"- {claim.text}{marker}")
    return out


def render_report(result: RunResult) -> str:
    ctx = result.context
    stats = result.stats
    lines: list[str] = []

    lines.append("# 철강산업 탄소중립 동향 센싱 리포트")
    lines.append("")
    lines.append(f"- 기간: {result.period.label()} ({result.period.days}일)")
    lines.append(f"- 기본 주제: {ctx.base_topic}")
    lines.append(
        f"- 추가 주제: {', '.join(ctx.extra_topics) if ctx.extra_topics else '없음'}"
        f" (결합 모드: {ctx.mode.upper()})"
    )
    if ctx.excludes:
        lines.append(f"- 제외어: {', '.join(ctx.excludes)}")
    lines.append(
        f"- 생성 시각: {ctx.started_at.astimezone(KST).strftime('%Y-%m-%d %H:%M')} KST"
    )
    lines.append("")

    lines.append("## 1. 사용한 질의어")
    lines.append("")
    for q in ctx.queries:
        lines.append(f"- `{q}`")
    lines.append("")

    lines.append("## 2. 수집 현황")
    lines.append("")
    lines.append("| 수집기 | 상태 | 원시 수집 | 비고 |")
    lines.append("| --- | --- | --- | --- |")
    for cr in result.collector_results:
        note = cr.skipped_reason or ("; ".join(cr.errors)[:120] if cr.errors else "-")
        lines.append(
            f"| {cr.collector} | {cr.status} | {len(cr.documents)} | {_md_cell(note)} |"
        )
    lines.append("")
    lines.append(
        f"- 원시 {stats.get('raw', 0)}건 → 중복 제거 후 {stats.get('deduped', 0)}건 "
        f"→ 기간 내 {stats.get('in_period', 0)}건 "
        f"→ 표시 {len(result.visible)}건(40점 이상)"
    )
    lines.append(f"- 본문 추출 성공: {stats.get('with_body', 0)}건")
    if stats.get("by_region"):
        region_text = ", ".join(f"{k} {v}건" for k, v in stats["by_region"].items())
        lines.append(f"- 지역별: {region_text}")
    if stats.get("by_doc_type"):
        type_text = ", ".join(f"{k} {v}건" for k, v in stats["by_doc_type"].items())
        lines.append(f"- 유형별: {type_text}")
    if stats.get("by_category"):
        cat_text = ", ".join(f"{k} {v}건" for k, v in stats["by_category"].items())
        lines.append(f"- 카테고리별: {cat_text}")
    if result.usage:
        usage = result.usage
        lines.append(
            f"- LLM 사용량: {usage.get('total_tokens', 0):,} 토큰"
            f" (호출 {usage.get('calls', 0)}회, 캐시 적중 {usage.get('cached_calls', 0)}회,"
            f" 추정 비용 USD {usage.get('estimated_cost_usd', 0)})"
        )
    lines.append("")

    lines.append("## 3. 관련도 순 문서 목록")
    lines.append("")
    visible = result.visible[: get_app_config().scoring.list_size]
    if visible:
        lines.extend(_doc_table(visible))
    else:
        lines.append("표시할 문서가 없습니다. 기간을 넓히거나 주제를 바꿔보세요.")
    lines.append("")

    citations = result.citation_map()

    lines.append("## 4. 대표 기사 요약·시사점")
    lines.append("")
    if not result.representative:
        reason = result.llm_skipped_reason or "대표 기사가 선정되지 않았습니다."
        lines.append(f"> {reason}")
        lines.append("")
    else:
        for category, members in result.by_category().items():
            lines.append(f"### [{category}]")
            lines.append("")
            for doc in members:
                num = citations.get(doc.id, 0)
                lines.append(
                    f"**[{num}] [{_md_cell(doc.title)}]({doc.url})**"
                )
                lines.append(
                    f"{doc.source_name} · {doc.doc_type} · {doc.region} · "
                    f"{_fmt_date(doc)} · 관련도 {doc.score:.1f}점"
                )
                lines.append("")
                insight = result.insights.get(doc.id)
                if insight is None:
                    lines.append("- 요약 생성 실패(부록 C 참조)")
                    lines.append("")
                    continue
                if insight.selection_reason:
                    lines.append(f"선정 사유: {insight.selection_reason}")
                    lines.append("")
                if insight.summary:
                    lines.append("요약")
                    lines.extend(_claim_lines(insight.summary, citations))
                    lines.append("")
                if insight.implications:
                    lines.append("시사점")
                    lines.extend(_claim_lines(insight.implications, citations))
                    lines.append("")

    lines.append("## 5. 종합 시사점")
    lines.append("")
    if result.synthesis is None:
        reason = result.llm_skipped_reason or "종합이 생성되지 않았습니다."
        lines.append(f"> {reason}")
        lines.append("")
    else:
        syn = result.synthesis
        if syn.headline:
            lines.append(f"**{syn.headline}**")
            lines.append("")
        if syn.trends:
            lines.append("### 핵심 트렌드")
            lines.append("")
            lines.extend(_claim_lines(syn.trends, citations))
            lines.append("")
        if syn.implications:
            lines.append("### 시사점")
            lines.append("")
            lines.extend(_claim_lines(syn.implications, citations))
            lines.append("")

    if result.representative:
        lines.append("### 출처")
        lines.append("")
        for doc in result.representative:
            num = citations.get(doc.id, 0)
            lines.append(
                f"[{num}] [{_md_cell(doc.title)}]({doc.url}) — "
                f"{doc.source_name}, {_fmt_date(doc)}"
            )
        lines.append("")

    lines.append("## 부록 A. 일자 미확인 문서")
    lines.append("")
    if result.date_unknown:
        lines.append("게재일을 파싱하지 못해 기간 필터에서 제외된 문서입니다.")
        lines.append("")
        for doc in result.date_unknown:
            lines.append(f"- [{_md_cell(doc.title)}]({doc.url}) — {doc.source_name}")
    else:
        lines.append("없음")
    lines.append("")

    lines.append("## 부록 B. 점수 미달 문서")
    lines.append("")
    hidden = result.hidden
    if hidden:
        lines.append(f"40점 미만 {len(hidden)}건은 기본 숨김입니다. 상위 10건만 표시합니다.")
        lines.append("")
        for doc in hidden[:10]:
            reason = doc.score_detail.get("off_topic") or "점수 미달"
            lines.append(
                f"- ({doc.score:.1f}점, {reason}) [{_md_cell(doc.title)}]({doc.url})"
            )
    else:
        lines.append("없음")
    lines.append("")

    lines.append("## 부록 C. 수집 실패 로그")
    lines.append("")
    failures = [
        (cr.collector, err)
        for cr in result.collector_results
        for err in cr.errors
    ]
    skipped = [
        (cr.collector, cr.skipped_reason)
        for cr in result.collector_results
        if cr.skipped_reason
    ]
    if not failures and not skipped:
        lines.append("없음")
    for name, reason in skipped:
        lines.append(f"- {name}: 건너뜀 — {reason}")
    for name, err in failures[:20]:
        lines.append(f"- {name}: {err}")
    lines.append("")

    return "\n".join(lines)


def write_outputs(result: RunResult, output_dir: Path) -> dict[str, Path]:
    """summary.md · report.md · articles.xlsx · raw.json을 저장한다.

    summary.md는 report.md를 대체하지 않고 별도로 추가 생성된다.
    """
    from .html_renderer import (
        html_to_pdf,
        render_report_html,
        render_summary_html,
    )
    from .summary_renderer import render_summary_one_page
    from .xlsx_renderer import write_xlsx

    import logging

    log = logging.getLogger(__name__)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    report_path = output_dir / "report.md"
    report_path.write_text(render_report(result), encoding="utf-8")
    paths["report"] = report_path

    summary_text, summary_meta = render_summary_one_page(result)
    summary_path = output_dir / "summary.md"
    summary_path.write_text(summary_text, encoding="utf-8")
    paths["summary"] = summary_path
    result.stats["summary_layout"] = summary_meta

    # HTML·PDF. 실패해도 Markdown 산출물은 이미 저장돼 있다.
    try:
        summary_html = render_summary_html(result)
        summary_html_path = output_dir / "summary.html"
        summary_html_path.write_text(summary_html, encoding="utf-8")
        paths["summary_html"] = summary_html_path

        report_html = render_report_html(result)
        report_html_path = output_dir / "report.html"
        report_html_path.write_text(report_html, encoding="utf-8")
        paths["report_html"] = report_html_path

        pdf = html_to_pdf(summary_html, output_dir / "summary.pdf", one_page=True)
        if pdf is not None:
            paths["summary_pdf"] = pdf
        pdf2 = html_to_pdf(report_html, output_dir / "report.pdf")
        if pdf2 is not None:
            paths["report_pdf"] = pdf2
    except Exception as exc:
        log.warning("HTML/PDF 생성 실패: %s", exc)

    try:
        xlsx_path = write_xlsx(result, output_dir / "articles.xlsx")
        if xlsx_path is not None:
            paths["xlsx"] = xlsx_path
    except Exception as exc:  # 엑셀 실패가 리포트를 막지 않는다
        import logging

        logging.getLogger(__name__).warning("articles.xlsx 생성 실패: %s", exc)

    raw: dict[str, Any] = {
        "context": result.context.to_dict(),
        "period": {
            "start": result.period.start.isoformat(),
            "end": result.period.end.isoformat(),
            "label": result.period.label(),
            "days": result.period.days,
        },
        "stats": result.stats,
        "collectors": [
            {
                "collector": cr.collector,
                "status": cr.status,
                "count": len(cr.documents),
                "skipped_reason": cr.skipped_reason,
                "errors": cr.errors,
            }
            for cr in result.collector_results
        ],
        "documents": [d.to_dict() for d in result.documents],
        "date_unknown": [d.to_dict() for d in result.date_unknown],
        "representative": [d.id for d in result.representative],
        "insights": {
            doc_id: insight.to_dict() for doc_id, insight in result.insights.items()
        },
        "synthesis": result.synthesis.to_dict() if result.synthesis else None,
        "usage": result.usage,
        "llm_skipped_reason": result.llm_skipped_reason,
    }
    raw_path = output_dir / "raw.json"
    raw_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["raw"] = raw_path
    return paths

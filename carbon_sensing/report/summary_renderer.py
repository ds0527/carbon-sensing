"""1장짜리 summary.md. report.md와 별개로 추가 생성되는 보고용 요약이다.

A4 1장을 넘지 않게 분량을 관리한다. 넘치면 트렌드 -> 시사점 순으로
글자 수를 줄이고, 대표 기사 건수는 줄이지 않는다(PRD 9-1).
"""

from __future__ import annotations

from typing import Any

from ..daterange import KST
from ..models import Document

# A4 1장 기준 대략치. 한글 기준으로 보수적으로 잡았다.
MAX_CHARS = 2600
MAX_LINES = 48


def _fmt_date(doc: Document) -> str:
    if doc.published_at is None:
        return "일자미확인"
    return doc.published_at.astimezone(KST).strftime("%Y-%m-%d")


def _one_line(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _claims(claims: list[Any], citations: dict[str, int], limit: int) -> list[str]:
    out: list[str] = []
    for claim in claims:
        marker = ""
        if claim.source_doc_id and claim.source_doc_id in citations:
            marker = f" [{citations[claim.source_doc_id]}]"
        out.append(f"- {_one_line(claim.text, limit)}{marker}")
    return out


def render_summary(result: Any, trend_limit: int = 110, impl_limit: int = 120) -> str:
    """1장 요약 본문. 분량이 넘으면 글자 수를 줄여 다시 만든다."""
    ctx = result.context
    stats = result.stats
    citations = result.citation_map()
    syn = result.synthesis

    lines: list[str] = []
    lines.append("# 철강산업 탄소중립 동향 Summary")
    lines.append("")
    lines.append(
        f"**기간** {result.period.label()} ({result.period.days}일) · "
        f"**작성** {ctx.started_at.astimezone(KST).strftime('%Y-%m-%d %H:%M')} KST"
    )

    topic_text = ", ".join(ctx.extra_topics) if ctx.extra_topics else "없음"
    lines.append(
        f"**주제** {ctx.base_topic} / 추가: {topic_text} [{ctx.mode.upper()}]"
    )
    lines.append(
        f"**수집** {stats.get('raw', 0)}건 → 유효 {stats.get('in_period', 0)}건 "
        f"→ 관련 {len(result.visible)}건 → 대표 {len(result.representative)}건"
    )
    if stats.get("by_category"):
        cat_text = " · ".join(f"{k} {v}" for k, v in stats["by_category"].items())
        lines.append(f"**카테고리** {cat_text}")
    lines.append("")

    if syn is not None and syn.headline:
        lines.append(f"> {_one_line(syn.headline, 160)}")
        lines.append("")

    if syn is not None and syn.trends:
        lines.append("## 핵심 트렌드")
        lines.append("")
        lines.extend(_claims(syn.trends, citations, trend_limit))
        lines.append("")

    lines.append("## 대표 기사")
    lines.append("")
    if not result.representative:
        lines.append(result.llm_skipped_reason or "대표 기사가 선정되지 않았습니다.")
        lines.append("")
    else:
        for category, members in result.by_category().items():
            lines.append(f"**[{category}]**")
            for doc in members:
                num = citations.get(doc.id, 0)
                insight = result.insights.get(doc.id)
                headline = ""
                if insight and insight.summary:
                    headline = _one_line(insight.summary[0].text, 90)
                elif doc.snippet:
                    headline = _one_line(doc.snippet, 90)
                lines.append(
                    f"- [{num}] [{_one_line(doc.title, 60)}]({doc.url}) "
                    f"— {doc.source_name}, {_fmt_date(doc)}"
                )
                if headline:
                    lines.append(f"  {headline}")
            lines.append("")

    if syn is not None and syn.implications:
        lines.append("## 시사점")
        lines.append("")
        lines.extend(_claims(syn.implications, citations, impl_limit))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_summary_one_page(result: Any) -> tuple[str, dict[str, Any]]:
    """A4 1장에 맞춘 요약과 분량 정보.

    1차 렌더가 넘치면 트렌드/시사점 문장 길이를 단계적으로 줄인다.
    대표 기사 건수는 건드리지 않는다.
    """
    attempts = [(110, 120), (85, 95), (65, 70), (50, 55)]
    text = ""
    for trend_limit, impl_limit in attempts:
        text = render_summary(result, trend_limit, impl_limit)
        if len(text) <= MAX_CHARS and text.count("\n") <= MAX_LINES:
            return text, {
                "chars": len(text),
                "lines": text.count("\n"),
                "trend_limit": trend_limit,
                "fits_one_page": True,
            }
    return text, {
        "chars": len(text),
        "lines": text.count("\n"),
        "trend_limit": attempts[-1][0],
        "fits_one_page": len(text) <= MAX_CHARS,
    }

"""HTML·PDF 렌더러. Jinja2 템플릿 + 듀오톤 CSS를 쓴다.

PDF는 Playwright Chromium으로 만든다. WeasyPrint는 Windows에서 GTK
런타임을 따로 요구해 쓰지 않는다. Chromium은 시스템 한글 폰트를
그대로 쓰므로 글자 깨짐이 없다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..daterange import KST
from ..models import Document
from ..processing.categorize import CATEGORIES, DEFAULT_CATEGORY

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# 듀오톤을 유지하면서 카테고리를 구분한다: 채움 / 외곽선 / 농도 / 파선
CATEGORY_CHIP = {
    "정책": "chip--fill",
    "설비": "",
    "기술": "chip--dim",
    "시장": "chip--ghost",
    DEFAULT_CATEGORY: "chip--dim",
}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _css() -> str:
    return (TEMPLATE_DIR / "base.css").read_text(encoding="utf-8")


def _fmt_date(doc: Document) -> str:
    if doc.published_at is None:
        return "일자미확인"
    return doc.published_at.astimezone(KST).strftime("%Y-%m-%d")


def _claims(claims: list[Any], citations: dict[str, int]) -> list[dict]:
    out = []
    for claim in claims:
        out.append(
            {
                "text": claim.text,
                "cite": citations.get(claim.source_doc_id or "", None),
            }
        )
    return out


def _grouped(result: Any, citations: dict[str, int]) -> list[dict]:
    """카테고리별 대표 기사 묶음."""
    groups = []
    for category in CATEGORIES + [DEFAULT_CATEGORY]:
        members = [d for d in result.representative if d.category == category]
        if not members:
            continue
        docs = []
        for doc in members:
            insight = result.insights.get(doc.id)
            first_line = ""
            if insight and insight.summary:
                first_line = insight.summary[0].text
            elif doc.snippet:
                first_line = doc.snippet[:120]
            docs.append(
                {
                    "cite": citations.get(doc.id, 0),
                    "title": doc.title,
                    "url": doc.url,
                    "source_name": doc.source_name,
                    "doc_type": doc.doc_type,
                    "region": doc.region,
                    "date": _fmt_date(doc),
                    "score": f"{doc.score:.1f}" if doc.score is not None else "-",
                    "first_line": first_line,
                    "selection_reason": insight.selection_reason if insight else None,
                    "summary": _claims(insight.summary, citations) if insight else [],
                    "implications": (
                        _claims(insight.implications, citations) if insight else []
                    ),
                }
            )
        groups.append(
            {
                "category": category,
                "chip": CATEGORY_CHIP.get(category, ""),
                "docs": docs,
            }
        )
    return groups


def _context(result: Any, list_limit: int | None = None) -> dict:
    ctx = result.context
    citations = result.citation_map()
    syn = result.synthesis
    visible = result.visible
    docs = visible if list_limit is None else visible[:list_limit]

    return {
        "css": _css(),
        "title": f"{ctx.base_topic} 동향 - {result.period.label()}",
        "base_topic": ctx.base_topic,
        "extra_topics": ctx.extra_topics,
        "excludes": ctx.excludes,
        "queries": ctx.queries,
        "mode": ctx.mode.upper(),
        "period_label": result.period.label(),
        "period_days": result.period.days,
        "generated_at": ctx.started_at.astimezone(KST).strftime("%Y-%m-%d %H:%M KST"),
        "stats": result.stats,
        "visible_count": len(visible),
        "category_counts": result.stats.get("by_category") or {},
        "representative": result.representative,
        "grouped": _grouped(result, citations),
        "headline": syn.headline if syn else "",
        "trends": _claims(syn.trends, citations) if syn else [],
        "implications": _claims(syn.implications, citations) if syn else [],
        "skipped_reason": result.llm_skipped_reason,
        "usage": result.usage or None,
        "documents": [
            {
                "score": f"{d.score:.1f}" if d.score is not None else "-",
                "category": d.category,
                "title": d.title,
                "url": d.url,
                "source_name": d.source_name,
                "doc_type": d.doc_type,
                "region": d.region,
                "date": _fmt_date(d),
                "duplicate_count": d.duplicate_count,
            }
            for d in docs
        ],
        "date_unknown": [
            {"title": d.title, "url": d.url, "source_name": d.source_name}
            for d in result.date_unknown[:20]
        ],
    }


def render_summary_html(result: Any) -> str:
    """1장 요약 HTML. report와 별개로 추가 생성된다."""
    return _env().get_template("summary.html.j2").render(**_context(result))


def render_report_html(result: Any, list_limit: int = 60) -> str:
    """상세 리포트 HTML."""
    return _env().get_template("report.html.j2").render(
        **_context(result, list_limit=list_limit)
    )


# A4 96dpi 기준 인쇄 가능 영역(여백 12mm/10mm 제외): 190mm x 273mm
PRINT_W_PX = 718
PRINT_H_PX = 1032
MIN_SCALE = 0.55  # 이보다 줄이면 읽기 어렵다


def _ensure_chromium_installed() -> None:
    """Streamlit Community Cloud 등은 `playwright install chromium`을 배포
    시점에 자동 실행해주지 않는다. 최초 실행 시 한 번만 내려받고, 이미
    설치돼 있으면 launch()가 바로 성공하므로 이 함수는 호출조차 안 된다."""
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
        capture_output=True,
    )


def _launch_chromium(p: Any):
    """Chromium이 없으면 한 번 설치를 시도한 뒤 재시도한다."""
    try:
        return p.chromium.launch()
    except Exception:
        _ensure_chromium_installed()
        return p.chromium.launch()


def _render_pdf_sync(html: str, path: Path, one_page: bool) -> Path | None:
    """실제 PDF 생성. 반드시 이벤트 루프가 없는 스레드에서 호출한다.

    one_page=True면 인쇄 폭에서 실제 높이를 재서 A4 한 장에 들어갈
    배율을 계산한다. 고정 배율로는 대표 기사 건수에 따라 2장이 된다.
    """
    from playwright.sync_api import sync_playwright

    margin = {"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"}
    try:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            page = browser.new_page(
                viewport={"width": PRINT_W_PX, "height": PRINT_H_PX}
            )
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")

            scale = 1.0
            if one_page:
                needed = page.evaluate(
                    "() => Math.max("
                    "document.documentElement.scrollHeight,"
                    "document.body.scrollHeight)"
                )
                if needed and needed > PRINT_H_PX:
                    # 2% 여유를 둬 경계에서 한 줄이 넘어가는 것을 막는다.
                    scale = max(MIN_SCALE, (PRINT_H_PX / needed) * 0.98)
                    log.info(
                        "1장 맞춤: 필요 %dpx / 가용 %dpx -> 배율 %.2f",
                        needed,
                        PRINT_H_PX,
                        scale,
                    )

            page.pdf(
                path=str(path),
                format="A4",
                print_background=True,
                margin=margin,
                prefer_css_page_size=True,
                scale=round(scale, 2),
            )
            browser.close()
    except Exception as exc:
        log.warning("PDF 생성 실패: %s", exc)
        return None
    return path


def html_to_pdf(html: str, path: Path, one_page: bool = False) -> Path | None:
    """Playwright Chromium으로 PDF를 만든다. 실패하면 None.

    write_outputs는 async 파이프라인 안에서도 불린다. Playwright 동기 API는
    실행 중인 asyncio 루프 안에서 쓸 수 없으므로 항상 워커 스레드로 넘긴다.
    """
    try:
        import playwright  # noqa: F401
    except ImportError:
        log.warning("playwright가 없어 PDF를 건너뜁니다.")
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_render_pdf_sync, html, path, one_page).result()


def _probe_sync() -> tuple[bool, str]:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = _launch_chromium(p)
            browser.close()
    except Exception:
        return False, "Chromium 미설치 - python -m playwright install chromium"
    return True, ""


def pdf_available() -> tuple[bool, str]:
    """PDF 생성 가능 여부와 사유. UI가 버튼을 끌지 결정하는 데 쓴다."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, "playwright 미설치 - pip install playwright"
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_probe_sync).result()

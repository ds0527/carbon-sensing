"""본문 탭: 수집 현황 / 문서 목록 / Summary / 다운로드."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ..daterange import KST
from ..models import Document
from ..processing.categorize import CATEGORIES, DEFAULT_CATEGORY
from .theme import FG_MUTED, chip_html

ALL_CATEGORIES = CATEGORIES + [DEFAULT_CATEGORY]


def _fmt_date(doc: Document) -> str:
    if doc.published_at is None:
        return "일자미확인"
    return doc.published_at.astimezone(KST).strftime("%Y-%m-%d")


def hero(result: Any = None) -> None:
    """16:9 듀오톤 배너."""
    if result is None:
        title = "철강산업 탄소중립 동향 센싱"
        sub = "왼쪽에서 기간과 주제를 설정하고 분석을 시작하세요."
    else:
        syn = result.synthesis
        title = (
            syn.headline
            if syn and syn.headline
            else f"{result.context.base_topic} 동향"
        )
        extra = ", ".join(result.context.extra_topics) or "추가 주제 없음"
        sub = (
            f"{result.period.label()} ({result.period.days}일) · {extra} · "
            f"대표 {len(result.representative)}건"
        )
    # 색은 인라인으로 직접 준다. 외부 스타일시트 캐스케이드에 맡기면
    # Streamlit이 헤더에 주입하는 자체 클래스와 우선순위 다툼이 생겨,
    # 같은 CSS인데도 문구에 따라 색이 먹거나 안 먹는 경우가 실제로 있었다
    # (기본 안내 문구일 때만 제목이 검게 보이는 버그로 재현됨).
    st.markdown(
        f"""<div class="cs-hero">
          <div class="kicker" style="color:#93C5FD">Carbon Neutrality Sensing · Steel</div>
          <h1 style="color:#FFFFFF">{title}</h1>
          <div class="sub" style="color:rgba(233,239,247,.78)">{sub}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def band(result: Any) -> None:
    stats = result.stats
    cells = [
        (stats.get("raw", 0), "수집"),
        (stats.get("deduped", 0), "중복제거"),
        (stats.get("in_period", 0), "기간 내"),
        (len(result.visible), "관련"),
        (len(result.representative), "대표"),
    ]
    html = "".join(
        f'<div class="cell"><span class="n">{n}</span><span class="k">{k}</span></div>'
        for n, k in cells
    )
    st.markdown(f'<div class="cs-band">{html}</div>', unsafe_allow_html=True)


def tab_collection(result: Any) -> None:
    """수집 현황: 소스별 결과와 실패 사유."""
    st.markdown('<div class="cs-h2">수집기별 결과</div>', unsafe_allow_html=True)
    rows = []
    for cr in result.collector_results:
        rows.append(
            {
                "수집기": cr.collector,
                "상태": cr.status,
                "원시 건수": len(cr.documents),
                "비고": cr.skipped_reason
                or ("; ".join(cr.errors)[:200] if cr.errors else ""),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    stats = result.stats
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown('<div class="cs-h2">지역별</div>', unsafe_allow_html=True)
        for k, v in (stats.get("by_region") or {}).items():
            st.write(f"{k} · **{v}**건")
    with c2:
        st.markdown('<div class="cs-h2">유형별</div>', unsafe_allow_html=True)
        for k, v in (stats.get("by_doc_type") or {}).items():
            st.write(f"{k} · **{v}**건")
    with c3:
        st.markdown('<div class="cs-h2">카테고리별</div>', unsafe_allow_html=True)
        for k, v in (stats.get("by_category") or {}).items():
            st.markdown(f"{chip_html(k)} **{v}**건", unsafe_allow_html=True)

    st.markdown('<div class="cs-h2">처리 요약</div>', unsafe_allow_html=True)
    st.write(
        f"본문 확보 **{stats.get('with_body', 0)}**건 · "
        f"일자 미확인 **{stats.get('date_unknown', 0)}**건 · "
        f"의미 유사도 {'적용' if stats.get('semantic_applied') else '미적용'}"
    )
    if result.usage:
        u = result.usage
        st.caption(
            f"LLM {u.get('total_tokens', 0):,} 토큰 · 호출 {u.get('calls', 0)}회 · "
            f"캐시 {u.get('cached_calls', 0)}회 · 추정 USD {u.get('estimated_cost_usd', 0)}"
        )
    if result.llm_skipped_reason:
        st.markdown(
            f'<div class="cs-note">{result.llm_skipped_reason}</div>',
            unsafe_allow_html=True,
        )


def tab_documents(result: Any) -> list[str] | None:
    """문서 목록. 반환값이 있으면 사용자가 대표 기사를 직접 고른 것이다."""
    st.markdown('<div class="cs-h2">필터</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    with c1:
        sort_by = st.radio(
            "정렬", ["관련도", "게재일"], horizontal=True, key="doc_sort"
        )
    with c2:
        cats = st.multiselect(
            "카테고리", ALL_CATEGORIES, default=[], key="doc_cats",
            placeholder="전체",
        )
    with c3:
        regions = st.multiselect(
            "지역", ["국내", "해외"], default=[], key="doc_regions",
            placeholder="전체",
        )
    with c4:
        show_all = st.checkbox(
            "점수 미달 포함", value=False, key="doc_show_all",
            help="40점 미만 문서까지 보여줍니다.",
        )

    docs = result.documents if show_all else result.visible
    if cats:
        docs = [d for d in docs if d.category in cats]
    if regions:
        docs = [d for d in docs if d.region in regions]
    if sort_by == "게재일":
        docs = sorted(
            docs,
            key=lambda d: (d.published_at is not None, d.published_at),
            reverse=True,
        )

    rep_ids = {d.id for d in result.representative}
    st.caption(f"{len(docs)}건")

    frame = pd.DataFrame(
        [
            {
                "선택": d.id in rep_ids,
                "점수": d.score,
                "카테고리": d.category,
                "제목": d.title,
                "링크": d.url,
                "출처": d.source_name,
                "유형": d.doc_type,
                "지역": d.region,
                "게재일": _fmt_date(d),
                "중복": d.duplicate_count,
                "본문": "O" if d.has_body else "",
                "점수근거": _score_tooltip(d),
                "id": d.id,
            }
            for d in docs
        ]
    )
    if frame.empty:
        st.markdown(
            '<div class="cs-note">조건에 맞는 문서가 없습니다. '
            "기간을 넓히거나 필터를 푸세요.</div>",
            unsafe_allow_html=True,
        )
        return None

    edited = st.data_editor(
        frame,
        use_container_width=True,
        hide_index=True,
        height=460,
        column_config={
            "선택": st.column_config.CheckboxColumn(
                "대표", help="대표 기사로 쓸 문서를 고르세요", width="small"
            ),
            "점수": st.column_config.NumberColumn(
                "점수", format="%.1f", width="small"
            ),
            "카테고리": st.column_config.TextColumn("카테고리", width="small"),
            "제목": st.column_config.TextColumn("제목", width="large"),
            "링크": st.column_config.LinkColumn(
                "링크", display_text="열기", width="small"
            ),
            "점수근거": st.column_config.TextColumn(
                "점수 근거", help="지표별 기여도", width="medium"
            ),
            "id": None,
        },
        disabled=[c for c in frame.columns if c != "선택"],
        key="doc_editor",
    )

    picked = [row["id"] for _, row in edited.iterrows() if row["선택"]]
    changed = set(picked) != rep_ids

    c1, c2 = st.columns([1, 3])
    with c1:
        regenerate = st.button(
            "이 선택으로 다시 생성",
            disabled=not picked or not changed,
            use_container_width=True,
        )
    with c2:
        if changed and picked:
            st.caption(f"{len(picked)}건 선택 — 수집 없이 요약만 다시 만듭니다.")
        elif not picked:
            st.caption("대표 기사를 하나 이상 고르세요.")

    if result.date_unknown:
        with st.expander(f"일자 미확인 {len(result.date_unknown)}건"):
            for d in result.date_unknown[:40]:
                st.markdown(f"- [{d.title}]({d.url}) — {d.source_name}")

    return picked if regenerate else None


def _score_tooltip(doc: Document) -> str:
    """지표별 기여도를 한 줄로. data_editor 셀에서 바로 읽게 한다."""
    detail = doc.score_detail or {}
    if detail.get("off_topic"):
        return f"제외: {detail['off_topic']}"
    contrib = detail.get("contributions") or {}
    if not contrib:
        return ""
    names = {
        "semantic": "의미",
        "keyword": "키워드",
        "source_trust": "출처",
        "recency": "최신",
        "spread": "확산",
    }
    return " ".join(f"{names.get(k, k)} {v}" for k, v in contrib.items())


def tab_summary(result: Any) -> None:
    """1장 Summary를 화면에 그린다. 인용 번호는 원문 링크로 이어진다."""
    if not result.representative:
        st.markdown(
            f'<div class="cs-note">'
            f'{result.llm_skipped_reason or "대표 기사가 선정되지 않았습니다."}'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    citations = result.citation_map()
    syn = result.synthesis

    if syn and syn.headline:
        st.markdown(f"### {syn.headline}")

    if syn and syn.trends:
        st.markdown('<div class="cs-h2">핵심 트렌드</div>', unsafe_allow_html=True)
        for claim in syn.trends:
            num = citations.get(claim.source_doc_id or "")
            mark = f' <span class="cs-cite">[{num}]</span>' if num else ""
            st.markdown(f"- {claim.text}{mark}", unsafe_allow_html=True)

    st.markdown('<div class="cs-h2">대표 기사</div>', unsafe_allow_html=True)
    for category, members in result.by_category().items():
        st.markdown(
            f"{chip_html(category)} <span style='color:{FG_MUTED};font-size:12px'>"
            f"{len(members)}건</span>",
            unsafe_allow_html=True,
        )
        for doc in members:
            num = citations.get(doc.id, 0)
            insight = result.insights.get(doc.id)
            st.markdown(
                f"""<div class="cs-art">
                  <div class="t"><span class="cs-cite">[{num}]</span>
                    <a href="{doc.url}" target="_blank">{doc.title}</a></div>
                  <div class="src">{doc.source_name} · {doc.doc_type} ·
                    {_fmt_date(doc)} · 관련도 {doc.score:.1f}</div>
                </div>""",
                unsafe_allow_html=True,
            )
            if insight:
                if insight.selection_reason:
                    st.caption(f"선정 사유: {insight.selection_reason}")
                with st.expander("요약·시사점", expanded=False):
                    if insight.summary:
                        st.markdown("**요약**")
                        for g in insight.summary:
                            st.markdown(f"- {g.text}")
                    if insight.implications:
                        st.markdown("**시사점**")
                        for g in insight.implications:
                            st.markdown(f"- {g.text}")

    if syn and syn.implications:
        st.markdown('<div class="cs-h2">시사점</div>', unsafe_allow_html=True)
        for claim in syn.implications:
            num = citations.get(claim.source_doc_id or "")
            mark = f' <span class="cs-cite">[{num}]</span>' if num else ""
            st.markdown(f"- {claim.text}{mark}", unsafe_allow_html=True)

    st.markdown('<div class="cs-h2">출처</div>', unsafe_allow_html=True)
    for doc in result.representative:
        st.markdown(
            f"[{citations.get(doc.id, 0)}] [{doc.title}]({doc.url}) — "
            f"{doc.source_name}, {_fmt_date(doc)}"
        )


DOWNLOADS = [
    ("summary", "1장 Summary (Markdown)", "text/markdown"),
    ("summary_pdf", "1장 Summary (PDF)", "application/pdf"),
    ("summary_html", "1장 Summary (HTML)", "text/html"),
    ("report", "상세 리포트 (Markdown)", "text/markdown"),
    ("report_pdf", "상세 리포트 (PDF)", "application/pdf"),
    ("report_html", "상세 리포트 (HTML)", "text/html"),
    ("xlsx", "전체 문서 목록 (Excel)",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("raw", "원본 데이터 (JSON)", "application/json"),
]


def tab_download(paths: dict[str, Path], result: Any) -> None:
    """산출물 다운로드. 일부가 없어도 있는 것만 내려준다."""
    layout = (result.stats or {}).get("summary_layout") or {}
    if layout and not layout.get("fits_one_page", True):
        st.warning(
            f"1장 Summary가 A4 한 장을 넘었습니다({layout.get('chars')}자). "
            "PDF는 자동 축소해 한 장으로 맞춥니다."
        )

    st.markdown('<div class="cs-h2">파일</div>', unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (key, label, mime) in enumerate(DOWNLOADS):
        path = paths.get(key)
        with cols[i % 2]:
            if path is None or not Path(path).exists():
                st.button(label, disabled=True, use_container_width=True,
                          key=f"dl_off_{key}")
                st.caption("↳ 생성되지 않았습니다.")
                continue
            data = Path(path).read_bytes()
            st.download_button(
                label,
                data=data,
                file_name=Path(path).name,
                mime=mime,
                use_container_width=True,
                key=f"dl_{key}",
            )

    st.markdown('<div class="cs-h2">저장 위치</div>', unsafe_allow_html=True)
    any_path = next((p for p in paths.values() if p), None)
    if any_path:
        st.code(str(Path(any_path).parent), language=None)

    if "summary_html" in paths and Path(paths["summary_html"]).exists():
        with st.expander("1장 Summary 미리보기", expanded=False):
            html = Path(paths["summary_html"]).read_text(encoding="utf-8")
            st.components.v1.html(html, height=900, scrolling=True)


def tab_history(on_load) -> None:
    """과거 실행 이력. 클릭하면 그 폴더를 불러온다."""
    from . import runner

    entries = runner.list_history()
    if not entries:
        st.markdown(
            '<div class="cs-note">아직 실행 이력이 없습니다.</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown('<div class="cs-h2">실행 이력</div>', unsafe_allow_html=True)
    for entry in entries:
        c1, c2, c3 = st.columns([3, 4, 1])
        with c1:
            st.write(f"**{entry.label}**")
            st.caption(entry.period)
        with c2:
            topics = ", ".join(entry.extra_topics) or "추가 주제 없음"
            st.write(topics)
            st.caption(f"관련 {entry.visible}건 · 대표 {entry.representative}건")
        with c3:
            if st.button("불러오기", key=f"hist_{entry.label}",
                         use_container_width=True):
                on_load(entry.path)

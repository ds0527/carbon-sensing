"""철강산업 탄소중립 동향 센싱 — Streamlit UI.

실행: streamlit run app.py

비즈니스 로직은 전부 carbon_sensing 패키지에 있다. 이 파일은 화면만
그리고 파이프라인을 부른다. CLI와 같은 경로를 쓰므로 결과가 갈리지 않는다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="철강 탄소중립 동향 센싱",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Streamlit Community Cloud는 API 키를 st.secrets로만 노출한다(.env 없음).
# Settings()는 OS 환경변수만 읽으므로, 배포 환경에서는 여기서 옮겨준다.
# 로컬처럼 secrets.toml이 없으면 st.secrets가 비어 있어 아무 일도 안 한다.
try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key.upper(), str(_value))
except Exception:
    pass

from carbon_sensing.config import ensure_utf8_stdout  # noqa: E402
from carbon_sensing.llm import TokenBudgetExceeded  # noqa: E402
from carbon_sensing.ui import runner, sidebar, views  # noqa: E402
from carbon_sensing.ui.theme import CSS  # noqa: E402

ensure_utf8_stdout()
logging.basicConfig(level=logging.WARNING)
st.markdown(CSS, unsafe_allow_html=True)

# 결과는 session_state에 둔다. 탭을 옮겨도 다시 실행되지 않는다.
st.session_state.setdefault("result", None)
st.session_state.setdefault("paths", {})
st.session_state.setdefault("progress_log", "")


def _run(settings: sidebar.Settings) -> None:
    period = sidebar.to_period(settings)
    if period is None:
        return

    tracker = runner.ProgressTracker()
    bar = st.progress(0.0, text="시작")
    log_box = st.empty()

    def on_progress(stage: str, message: str) -> None:
        tracker(stage, message)
        bar.progress(min(tracker.fraction, 1.0), text=f"{tracker.label} · {message}")
        log_box.markdown(tracker.rendered())

    try:
        result, paths = runner.run_pipeline_sync(
            period=period,
            extra_topics=settings.extra_topics,
            mode=settings.mode,
            excludes=settings.excludes,
            top_k=settings.top_k,
            collectors=settings.collectors,
            use_llm=settings.use_llm,
            use_cache=settings.use_cache,
            progress=on_progress,
        )
    except TokenBudgetExceeded as exc:
        bar.empty()
        st.error(f"토큰 예산 초과로 중단했습니다.\n\n{exc}")
        return
    except Exception as exc:  # noqa: BLE001
        bar.empty()
        st.error(f"실행 중 오류가 발생했습니다: {type(exc).__name__}: {exc}")
        st.caption("자세한 내용은 터미널 로그를 확인하세요.")
        return

    bar.progress(1.0, text="완료")
    st.session_state["result"] = result
    st.session_state["paths"] = paths
    st.session_state["progress_log"] = tracker.rendered()
    st.rerun()


def _regenerate(pick_ids: list[str]) -> None:
    """대표 기사만 바꿔 다시 생성. 수집을 건너뛴다."""
    paths = st.session_state.get("paths") or {}
    any_path = next((p for p in paths.values() if p), None)
    if any_path is None:
        st.error("재생성할 출력 폴더를 찾을 수 없습니다.")
        return

    tracker = runner.ProgressTracker()
    bar = st.progress(0.0, text="시작")

    def on_progress(stage: str, message: str) -> None:
        tracker(stage, message)
        bar.progress(min(tracker.fraction, 1.0), text=f"{tracker.label} · {message}")

    try:
        result, new_paths = runner.rerun_sync(
            Path(any_path).parent, None, pick_ids, on_progress
        )
    except TokenBudgetExceeded as exc:
        bar.empty()
        st.error(f"토큰 예산 초과로 중단했습니다.\n\n{exc}")
        return
    except Exception as exc:  # noqa: BLE001
        bar.empty()
        st.error(f"재생성 실패: {type(exc).__name__}: {exc}")
        return

    bar.empty()
    st.session_state["result"] = result
    st.session_state["paths"] = new_paths
    st.rerun()


def _load_history(folder: Path) -> None:
    """과거 실행을 불러온다. LLM을 부르지 않고 파일만 읽는다."""
    from carbon_sensing.rerun import RerunError, load_run
    from carbon_sensing.storage import Database

    try:
        with Database() as db:
            result = load_run(folder, db=db)
    except RerunError as exc:
        st.error(f"불러오기 실패: {exc}")
        return

    # 저장돼 있던 요약·대표기사를 함께 복원한다.
    import json

    raw = json.loads((folder / "raw.json").read_text(encoding="utf-8"))
    by_id = {d.id: d for d in result.documents}
    result.representative = [
        by_id[i] for i in raw.get("representative", []) if i in by_id
    ]
    from carbon_sensing.llm.service import DocumentInsight, GroundedText, Synthesis

    def _claims(items):
        return [
            GroundedText(
                text=c["text"],
                source_doc_id=c.get("source_doc_id"),
                verified=c.get("verified", True),
            )
            for c in items
        ]

    result.insights = {
        doc_id: DocumentInsight(
            doc_id=doc_id,
            summary=_claims(v.get("summary", [])),
            implications=_claims(v.get("implications", [])),
            selection_reason=v.get("selection_reason"),
        )
        for doc_id, v in (raw.get("insights") or {}).items()
    }
    syn = raw.get("synthesis")
    result.synthesis = (
        Synthesis(
            headline=syn.get("headline", ""),
            trends=_claims(syn.get("trends", [])),
            implications=_claims(syn.get("implications", [])),
        )
        if syn
        else None
    )
    result.usage = raw.get("usage") or {}
    result.llm_skipped_reason = raw.get("llm_skipped_reason")

    st.session_state["result"] = result
    st.session_state["paths"] = {
        key: folder / name
        for key, name in (
            ("summary", "summary.md"),
            ("summary_pdf", "summary.pdf"),
            ("summary_html", "summary.html"),
            ("report", "report.md"),
            ("report_pdf", "report.pdf"),
            ("report_html", "report.html"),
            ("xlsx", "articles.xlsx"),
            ("raw", "raw.json"),
        )
        if (folder / name).exists()
    }
    st.rerun()


settings = sidebar.render()
result = st.session_state.get("result")

views.hero(result)

if settings.submitted:
    _run(settings)
    st.stop()

if result is None:
    tab_start, tab_hist = st.tabs(["시작하기", "실행 이력"])
    with tab_start:
        st.markdown(
            """왼쪽 사이드바에서 **기간**과 **추가 주제**를 정하고
            **동향 분석 시작**을 누르세요.

1. 국내외 기사·보도자료·보고서·논문을 수집합니다.
2. 중복을 제거하고 관련도 순으로 정렬합니다.
3. 설비·정책·기술·시장 카테고리로 나눕니다.
4. 대표 기사를 골라 요약과 시사점을 만듭니다.
5. **1장 Summary**와 **상세 리포트**를 각각 생성합니다."""
        )
        rows = runner.collector_availability()
        off = [r for r in rows if not r["available"]]
        if off:
            st.markdown('<div class="cs-h2">비활성 소스</div>', unsafe_allow_html=True)
            for r in off:
                st.caption(f"{r['label']} — {r['reason']}")
    with tab_hist:
        views.tab_history(_load_history)
else:
    views.band(result)
    tabs = st.tabs(
        ["Summary", "문서 목록", "수집 현황", "다운로드", "실행 이력"]
    )
    with tabs[0]:
        views.tab_summary(result)
    with tabs[1]:
        picked = views.tab_documents(result)
        if picked:
            _regenerate(picked)
    with tabs[2]:
        views.tab_collection(result)
        if st.session_state.get("progress_log"):
            with st.expander("진행 로그"):
                st.markdown(st.session_state["progress_log"])
    with tabs[3]:
        views.tab_download(st.session_state.get("paths") or {}, result)
    with tabs[4]:
        views.tab_history(_load_history)

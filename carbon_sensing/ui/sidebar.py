"""사이드바 설정 패널.

한 화면(스크롤 없이)에 들어가도록 자주 안 바꾸는 항목은 접어두고,
체크박스·라디오 같은 좁은 위젯은 여러 열로 묶어 세로 길이를 줄인다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import streamlit as st

from ..config import get_topics
from ..daterange import DateRangeError, build_range, preset_range
from . import runner

PRESET_BUTTONS = [
    ("7d", "지난 7일"),
    ("30d", "지난 30일"),
    ("90d", "지난 90일"),
    ("last-month", "지난달"),
    ("this-quarter", "이번 분기"),
]


@dataclass
class Settings:
    """사이드바가 모아 돌려주는 실행 조건."""

    date_from: date
    date_to: date
    extra_topics: list[str] = field(default_factory=list)
    mode: str = "and"
    excludes: list[str] = field(default_factory=list)
    collectors: list[str] = field(default_factory=list)
    top_k: int = 5
    use_llm: bool = True
    use_cache: bool = True
    submitted: bool = False


def _apply_preset(name: str) -> None:
    period = preset_range(name)
    from ..daterange import KST

    st.session_state["date_from"] = period.start.astimezone(KST).date()
    st.session_state["date_to"] = period.end.astimezone(KST).date()


def render() -> Settings:
    topics = get_topics()
    today = date.today()

    st.session_state.setdefault("date_from", today - timedelta(days=29))
    st.session_state.setdefault("date_to", today)

    with st.sidebar:
        # --- 기간 ---
        st.markdown('<div class="cs-h2">기간</div>', unsafe_allow_html=True)
        labels = {key: label for key, label in PRESET_BUTTONS}
        chosen = st.selectbox(
            "프리셋",
            options=[""] + [k for k, _ in PRESET_BUTTONS],
            format_func=lambda k: labels.get(k, "직접 지정"),
            key="preset_pick",
            label_visibility="collapsed",
        )
        if chosen and st.session_state.get("_applied_preset") != chosen:
            st.session_state["_applied_preset"] = chosen
            _apply_preset(chosen)
            st.rerun()

        c1, c2 = st.columns(2)
        with c1:
            date_from = st.date_input("시작일", key="date_from", format="YYYY-MM-DD")
        with c2:
            date_to = st.date_input("종료일", key="date_to", format="YYYY-MM-DD")

        days = (date_to - date_from).days + 1
        if date_from > date_to:
            st.error("시작일이 종료일보다 뒤입니다.")
        elif days > 90:
            st.warning(f"{days}일 — 수집·API 사용량이 크게 늘어납니다.")
        else:
            st.caption(f"{days}일 · 기본 주제: {topics.base_topic}")

        # --- 주제 ---
        st.markdown('<div class="cs-h2">추가 주제</div>', unsafe_allow_html=True)
        extra_text = st.text_area(
            "추가 주제 (한 줄에 하나)",
            key="extra_topics_text",
            placeholder="CBAM\n배출권 거래제\n수소환원제철",
            height=68,
            label_visibility="collapsed",
        )
        extra_topics = [t.strip() for t in (extra_text or "").splitlines() if t.strip()]

        c3, c4 = st.columns([3, 4])
        with c3:
            mode = st.radio(
                "결합",
                ["and", "or"],
                key="mode",
                horizontal=True,
                label_visibility="collapsed",
                format_func=lambda m: "AND" if m == "and" else "OR",
            )
        with c4:
            excludes_text = st.text_input(
                "제외어", key="excludes_text", placeholder="제외어(쉼표 구분)",
                label_visibility="collapsed",
            )
        excludes = [e.strip() for e in (excludes_text or "").split(",") if e.strip()]

        with st.expander("주제 프로필 저장·불러오기"):
            profiles = runner.list_profiles()
            if profiles:
                pick = st.selectbox(
                    "저장된 프로필", ["(사용 안 함)"] + list(profiles), key="profile_pick"
                )
                if pick != "(사용 안 함)" and st.button(
                    "불러오기", use_container_width=True
                ):
                    prof = profiles[pick]
                    st.session_state["extra_topics_text"] = "\n".join(
                        prof.get("extra_topics", [])
                    )
                    st.session_state["mode"] = prof.get("mode", "and")
                    st.session_state["excludes_text"] = ", ".join(
                        prof.get("excludes", [])
                    )
                    st.rerun()
            new_name = st.text_input("새 이름으로 저장", key="profile_name")
            if st.button("저장", use_container_width=True):
                try:
                    runner.save_profile(new_name, extra_topics, mode, excludes)
                    st.success(f"'{new_name}' 저장됨")
                except ValueError as exc:
                    st.error(str(exc))

        # --- 수집 소스·분석: 기본값이 이미 실행 가능하므로 접어 둔다.
        # (2열 그리드 + 슬라이더 + 체크박스만으로도 세로 공간을 꽤 먹어서,
        # 항상 펼쳐두면 사이드바가 한 화면을 넘어간다)
        rows = runner.collector_availability()
        llm_ok, llm_reason = runner.llm_availability()
        top_k = st.session_state.get("top_k", 5)
        use_llm = st.session_state.get("use_llm", llm_ok)
        use_cache = st.session_state.get("use_cache", True)

        n_selected = sum(1 for r in rows if st.session_state.get(f"col_{r['name']}", r["available"]))
        summary = f"수집 소스 {n_selected}개 · 대표 {top_k}건 · LLM {'켜짐' if use_llm else '꺼짐'}"
        with st.expander(f"수집·분석 설정 — {summary}"):
            st.markdown("**수집 소스**")
            cols = st.columns(2)
            selected = []
            for i, row in enumerate(rows):
                with cols[i % 2]:
                    if row["available"]:
                        if st.checkbox(
                            row["label"], value=True, key=f"col_{row['name']}",
                            help=row["detail"],
                        ):
                            selected.append(row["name"])
                    else:
                        st.checkbox(
                            row["label"], value=False, disabled=True,
                            key=f"col_{row['name']}",
                            help=f"{row['detail']} — {row['reason']}",
                        )

            st.markdown("**분석**")
            top_k = st.slider("대표 기사 건수", 3, 10, 5, key="top_k")
            c5, c6 = st.columns(2)
            with c5:
                if llm_ok:
                    use_llm = st.checkbox("LLM 요약", value=True, key="use_llm")
                else:
                    st.checkbox(
                        "LLM 요약", value=False, disabled=True, key="use_llm",
                        help=llm_reason,
                    )
                    use_llm = False
            with c6:
                use_cache = st.checkbox(
                    "캐시 사용", value=True, key="use_cache",
                    help="같은 조건 재실행 시 API를 부르지 않습니다.",
                )
            if use_llm:
                est = runner.estimate_cost(len(extra_topics) or 12, top_k)
                st.caption(f"예상 {est['total_tokens']:,} 토큰 · 약 USD {est['cost_usd']}")
                if est["over_budget"]:
                    st.error(
                        f"예상 토큰이 상한({est['budget']:,})을 넘습니다. "
                        "대표 기사 수를 줄이거나 MAX_TOKENS_PER_RUN을 올리세요."
                    )

        if not selected:
            st.caption("수집 소스를 하나 이상 선택하세요.")

        submitted = st.button(
            "동향 분석 시작", type="primary", use_container_width=True,
            disabled=(date_from > date_to or not selected),
        )

    return Settings(
        date_from=date_from,
        date_to=date_to,
        extra_topics=extra_topics,
        mode=mode,
        excludes=excludes,
        collectors=selected,
        top_k=top_k,
        use_llm=use_llm,
        use_cache=use_cache,
        submitted=submitted,
    )


def to_period(settings: Settings):
    """Settings의 날짜를 DateRange로. 실패하면 메시지를 띄운다."""
    try:
        return build_range(settings.date_from, settings.date_to)
    except DateRangeError as exc:
        st.error(f"기간 오류: {exc}")
        return None

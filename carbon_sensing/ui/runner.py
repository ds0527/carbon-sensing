"""UI와 파이프라인 사이의 다리.

파이프라인 로직은 건드리지 않는다. UI는 run_and_write만 부르고,
진행 상황은 콜백으로 받아 화면에 그린다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from ..config import PACKAGE_DIR, get_app_config, get_settings, reset_config_cache
from ..daterange import DateRange
from ..llm.client import PRICING_PER_1K, count_tokens
from ..report import OUTPUT_ROOT

log = logging.getLogger(__name__)

# 파이프라인이 알리는 단계 순서. 진행률 계산에 쓴다.
STAGES = [
    "질의어",
    "수집",
    "중복제거",
    "기간필터",
    "본문추출",
    "분류",
    "임베딩",
    "스코어링",
    "대표선정",
    "요약",
    "종합",
]

STAGE_LABEL = {
    "질의어": "질의어 확장",
    "수집": "문서 수집",
    "중복제거": "중복 제거",
    "기간필터": "기간 필터",
    "본문추출": "본문 추출",
    "분류": "카테고리 분류",
    "임베딩": "의미 유사도",
    "스코어링": "관련도 점수",
    "대표선정": "대표 기사 선정",
    "요약": "요약·시사점",
    "종합": "전체 종합",
}


@dataclass
class ProgressTracker:
    """단계별 진행 상황. 콜백으로 채워지고 UI가 읽는다."""

    lines: list[tuple[str, str]] = field(default_factory=list)
    current: str = ""

    def __call__(self, stage: str, message: str) -> None:
        self.current = stage
        self.lines.append((stage, message))

    @property
    def fraction(self) -> float:
        if not self.current:
            return 0.0
        if self.current in STAGES:
            return (STAGES.index(self.current) + 1) / len(STAGES)
        return 1.0

    @property
    def label(self) -> str:
        return STAGE_LABEL.get(self.current, self.current)

    def rendered(self) -> str:
        out = []
        for stage, message in self.lines:
            out.append(f"**{STAGE_LABEL.get(stage, stage)}** · {message}")
        return "\n\n".join(out)


def collector_availability() -> list[dict[str, Any]]:
    """수집기별 사용 가능 여부와 사유. 키 값은 절대 노출하지 않는다."""
    settings = get_settings()
    missing = settings.missing_keys()
    rows = []
    for name, need, detail in (
        ("naver_news", "네이버 뉴스", "국내 뉴스 (네이버 검색)"),
        ("web_search", "웹 검색", "국내외 웹 검색 (Tavily)"),
        ("rss", "RSS 피드", "기관·언론 RSS 피드"),
        ("openalex", "학술 논문", "학술 논문 (OpenAlex)"),
        ("gdelt", "해외 뉴스", "해외 뉴스 (GDELT)"),
    ):
        keys = missing.get(name, [])
        rows.append(
            {
                "name": name,
                "label": need,
                "detail": detail,
                "available": not keys,
                "reason": (
                    f".env에 {', '.join(keys)} 없음" if keys else ""
                ),
            }
        )
    return rows


def llm_availability() -> tuple[bool, str]:
    """LLM 사용 가능 여부. 키가 없으면 이름만 알려준다."""
    if not get_settings().openai_api_key:
        return False, ".env에 OPENAI_API_KEY 없음"
    return True, ""


def estimate_cost(queries: int, top_k: int, doc_count: int = 30) -> dict[str, Any]:
    """실행 전 예상 토큰·비용. 어림값이며 실행 후 실제값으로 대체된다."""
    settings = get_settings()
    cfg = get_app_config()
    body = cfg.report.body_char_limit

    # 대표선정 1회 + 기사별 요약 top_k회 + 종합 1회
    select_in = doc_count * 800
    summarize_in = top_k * body
    synth_in = top_k * 600
    prompt_tokens = count_tokens("가" * (select_in + summarize_in + synth_in), "gpt-4o")
    completion_tokens = (top_k + 2) * 700

    in_rate, out_rate = PRICING_PER_1K.get(settings.openai_model, (0.0025, 0.01))
    embed_tokens = doc_count * 400
    embed_rate = PRICING_PER_1K.get(settings.openai_embedding_model, (0.00002, 0))[0]

    cost = (
        prompt_tokens / 1000 * in_rate
        + completion_tokens / 1000 * out_rate
        + embed_tokens / 1000 * embed_rate
    )
    total = prompt_tokens + completion_tokens + embed_tokens
    return {
        "total_tokens": total,
        "cost_usd": round(cost, 3),
        "over_budget": total > settings.max_tokens_per_run,
        "budget": settings.max_tokens_per_run,
    }


# --- 주제 프로필 저장·불러오기 (topics.yaml) ---

TOPICS_PATH = PACKAGE_DIR / "topics.yaml"


def list_profiles() -> dict[str, dict[str, Any]]:
    from ..config import get_topics

    return dict(get_topics().profiles)


def save_profile(name: str, extra_topics: list[str], mode: str, excludes: list[str]) -> None:
    """topics.yaml의 profiles에 추가하거나 덮어쓴다."""
    name = (name or "").strip()
    if not name:
        raise ValueError("프로필 이름을 입력하세요.")
    data = yaml.safe_load(TOPICS_PATH.read_text(encoding="utf-8")) or {}
    profiles = data.setdefault("profiles", {})
    profiles[name] = {
        "extra_topics": extra_topics,
        "mode": mode,
        "excludes": excludes,
    }
    TOPICS_PATH.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    reset_config_cache()


def delete_profile(name: str) -> None:
    data = yaml.safe_load(TOPICS_PATH.read_text(encoding="utf-8")) or {}
    profiles = data.get("profiles") or {}
    if name in profiles:
        del profiles[name]
        TOPICS_PATH.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        reset_config_cache()


# --- 실행 이력 ---


@dataclass
class HistoryEntry:
    path: Path
    label: str
    period: str
    extra_topics: list[str]
    visible: int
    representative: int
    has_summary: bool


def list_history(limit: int = 30) -> list[HistoryEntry]:
    """outputs/ 폴더를 훑어 과거 실행을 최신순으로 돌려준다."""
    if not OUTPUT_ROOT.exists():
        return []
    entries: list[HistoryEntry] = []
    for folder in sorted(
        (p for p in OUTPUT_ROOT.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    ):
        raw_path = folder / "raw.json"
        if not raw_path.exists():
            continue
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        ctx = raw.get("context", {})
        stats = raw.get("stats", {})
        entries.append(
            HistoryEntry(
                path=folder,
                label=folder.name,
                period=(raw.get("period", {}) or {}).get("label", "?"),
                extra_topics=list(ctx.get("extra_topics", [])),
                visible=stats.get("visible", 0),
                representative=len(raw.get("representative", [])),
                has_summary=(folder / "summary.md").exists(),
            )
        )
        if len(entries) >= limit:
            break
    return entries


def run_pipeline_sync(
    period: DateRange,
    extra_topics: list[str],
    mode: str,
    excludes: list[str],
    top_k: int,
    collectors: list[str],
    use_llm: bool,
    use_cache: bool,
    progress: Callable[[str, str], None],
):
    """CLI와 동일한 경로를 부른다. UI에 로직을 복사하지 않는다."""
    from ..pipeline import run_and_write

    return run_and_write(
        period,
        extra_topics=extra_topics or None,
        mode=mode,
        excludes=excludes or None,
        top_n=top_k,
        collectors=collectors or None,
        progress=progress,
        use_llm=use_llm,
        use_cache=use_cache,
    )


def rerun_sync(output_dir: Path, top_k: int | None, pick_ids: list[str] | None,
               progress: Callable[[str, str], None], use_cache: bool = True):
    """대표 기사만 바꿔 다시 생성. 수집을 하지 않는다."""
    import asyncio

    from ..rerun import rerun_llm

    return asyncio.run(
        rerun_llm(
            output_dir,
            top_n=top_k,
            pick_ids=pick_ids,
            progress=progress,
            use_cache=use_cache,
        )
    )

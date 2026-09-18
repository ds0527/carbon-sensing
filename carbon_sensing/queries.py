"""질의어 생성. 1단계는 LLM 없이 규칙으로만 만든다."""

from __future__ import annotations

from .config import get_app_config, get_topics


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def build_queries(
    extra_topics: list[str] | None = None,
    mode: str = "and",
    *,
    max_queries: int | None = None,
) -> list[str]:
    """기본 주제 질의어와 추가 주제를 결합한다.

    - and: 기본 주제 질의어에 추가 주제를 덧붙인다(교집합 검색).
    - or : 기본 주제 질의어와 추가 주제를 각각 따로 돌린다(합집합 검색).
    한글 질의어에는 한글 주제를, 영문 질의어에는 영문 주제를 붙이는 게
    이상적이지만 추가 주제의 언어를 알 수 없으므로 양쪽 모두에 붙인다.
    """
    topics = get_topics()
    limit = max_queries or get_app_config().collect.max_queries
    extras = _dedupe_keep_order(list(extra_topics or []))
    base = _dedupe_keep_order(list(topics.base_queries_ko) + list(topics.base_queries_en))

    if not extras:
        return base[:limit]

    normalized_mode = mode.strip().lower()
    if normalized_mode not in ("and", "or"):
        raise ValueError(f"mode는 and 또는 or만 됩니다: {mode!r}")

    if normalized_mode == "or":
        # 기본 주제와 추가 주제를 각각 독립적으로 검색한다.
        combined = base + [f"철강 {e}" for e in extras] + extras
        return _dedupe_keep_order(combined)[:limit]

    # and: 기본 주제 질의어 x 추가 주제. 질의어가 폭발하지 않게 라운드로빈으로 섞는다.
    pairs: list[str] = []
    for extra in extras:
        for b in base:
            # 기본 질의어에 이미 그 주제가 들어 있으면 중복해서 붙이지 않는다
            # ("CBAM steel" + "CBAM" -> "CBAM steel CBAM" 방지).
            if extra.lower() in b.lower():
                pairs.append(b)
            else:
                pairs.append(f"{b} {extra}")
    interleaved: list[str] = []
    per_extra = max(1, len(base))
    for i in range(per_extra):
        for j, _extra in enumerate(extras):
            idx = j * per_extra + i
            if idx < len(pairs):
                interleaved.append(pairs[idx])
    return _dedupe_keep_order(interleaved)[:limit]


def split_language(queries: list[str]) -> tuple[list[str], list[str]]:
    """한글이 섞인 질의어와 영문 전용 질의어를 나눈다(수집기 라우팅용)."""
    ko: list[str] = []
    en: list[str] = []
    for q in queries:
        if any("가" <= ch <= "힣" for ch in q):
            ko.append(q)
        else:
            en.append(q)
    return ko, en

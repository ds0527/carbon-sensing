"""관련도 스코어링. PRD 6장의 5개 지표를 0~100으로 합산한다.

semantic(의미 유사도)은 임베딩이 있을 때만 계산된다(scoring/embedding.py).
- 있으면: 5개 지표를 PRD 가중치(40/20/20/10/10) 그대로 쓴다.
- 없으면: semantic을 빼고 나머지 4개를 재정규화한다(OPENAI_API_KEY 미설정 시).
문서별로 임베딩이 빠진 경우에도 그 문서만 4개 지표로 재정규화해, 인프라
실패가 점수를 깎지 않게 한다.

주제 판정은 점수 기여가 아니라 게이트다. "철강산업의 탄소중립"이
주제이므로 철강 키워드와 탄소 키워드가 모두 걸려야 주제 문서로 본다.
게이트가 없으면 출처 신뢰도(26.7점) + 최신성(16점)만으로 무관한 문서가
40점 임계값을 넘는다.
"""

from __future__ import annotations

import math

from ..config import get_app_config, get_sources, get_topics
from ..daterange import DateRange
from ..models import Document
from .matching import match_keywords, normalize

# 임베딩 없이 계산 가능한 지표
BASE_METRICS = ("keyword", "source_trust", "recency", "spread")
# 임베딩이 있을 때 쓰는 전체 지표
ALL_METRICS = ("semantic",) + BASE_METRICS
# 하위 호환(1단계 테스트가 쓰던 이름)
AVAILABLE_METRICS = BASE_METRICS


def topic_gate(
    doc: Document, steel: list[str], carbon: list[str], excludes: list[str]
) -> tuple[str | None, dict]:
    """주제 적합 여부와 근거. 반환값이 문자열이면 주제 밖이다."""
    title = normalize(doc.title)
    full = normalize(f"{doc.title} {doc.searchable_text()}")

    for word in excludes:
        w = (word or "").strip().lower()
        if w and w in title:
            return f"제외어 '{word}'", {}

    steel_hits, steel_matched = match_keywords(full, steel)
    carbon_hits, carbon_matched = match_keywords(full, carbon)
    evidence = {"steel_matched": steel_matched[:6], "carbon_matched": carbon_matched[:6]}

    if not steel_matched:
        return "철강 키워드 없음", evidence
    if not carbon_matched:
        return "탄소 키워드 없음", evidence
    return None, evidence


def keyword_component(
    doc: Document, keywords: list[str], title_w: float, body_w: float
) -> tuple[float, dict]:
    """제목 x2.0, 본문 x1.0 가중 매칭. 0~1로 포화 함수를 씌운다."""
    title = normalize(doc.title)
    body = normalize(doc.searchable_text())
    title_hits, title_matched = match_keywords(title, keywords)
    body_hits, body_matched = match_keywords(body, keywords)
    raw = title_hits * title_w + body_hits * body_w
    # 히트가 쌓일수록 완만해지는 포화 곡선. raw=6이면 약 0.75.
    value = raw / (raw + 2.0) if raw > 0 else 0.0
    return value, {
        "title_hits": title_hits,
        "body_hits": body_hits,
        "matched": sorted(set(title_matched + body_matched))[:12],
    }


def recency_component(doc: Document, period: DateRange) -> tuple[float, dict]:
    """기간 안에서 끝에 가까울수록 1에 가깝다. 일자 미확인은 중간값."""
    if doc.published_at is None:
        return 0.5, {"note": "일자미확인(중간값)"}
    span = (period.end - period.start).total_seconds()
    if span <= 0:
        return 1.0, {}
    offset = (doc.published_at - period.start).total_seconds()
    value = min(1.0, max(0.0, offset / span))
    return value, {"days_from_start": round(offset / 86400, 1)}


def spread_component(doc: Document, max_duplicates: int) -> tuple[float, dict]:
    """중복보도 언론사 수의 log 스케일."""
    detail = {"duplicate_count": doc.duplicate_count}
    if max_duplicates <= 1:
        return (0.0 if doc.duplicate_count <= 1 else 1.0), detail
    value = math.log1p(doc.duplicate_count - 1) / math.log1p(max_duplicates - 1)
    return min(1.0, value), detail


def score_documents(
    documents: list[Document],
    period: DateRange,
    excludes: list[str] | None = None,
    semantic_scores: dict[str, float] | None = None,
) -> list[Document]:
    """모든 문서에 score와 score_detail을 채워 점수 내림차순으로 반환한다.

    semantic_scores가 None이면 임베딩 단계를 돌지 않은 것으로 보고
    나머지 4개 지표를 재정규화한다. 이때만 본문 누락 패널티를 점수 전체에
    적용한다(의미 유사도가 없어 그 신뢰 저하를 표현할 다른 자리가 없다).
    """
    cfg = get_app_config().scoring
    topics = get_topics()
    sources = get_sources()
    weights = cfg.weights.as_dict()
    excludes = list(excludes or []) + list(topics.default_excludes)
    all_keywords = topics.all_keywords

    max_dup = max((d.duplicate_count for d in documents), default=1)

    for doc in documents:
        off_topic, gate_evidence = topic_gate(
            doc, topics.steel_keywords, topics.carbon_keywords, excludes
        )

        kw_value, kw_detail = keyword_component(
            doc, all_keywords, cfg.title_weight, cfg.body_weight
        )
        tier_name, tier_value = sources.tier_of(doc.url)
        rec_value, rec_detail = recency_component(doc, period)
        spr_value, spr_detail = spread_component(doc, max_dup)

        components = {
            "keyword": kw_value,
            "source_trust": tier_value,
            "recency": rec_value,
            "spread": spr_value,
        }

        semantic_value = (
            semantic_scores.get(doc.id) if semantic_scores is not None else None
        )
        if semantic_value is not None:
            components["semantic"] = semantic_value
            metrics = ALL_METRICS
        else:
            metrics = BASE_METRICS

        active = {k: weights[k] for k in metrics}
        total_w = sum(active.values()) or 1.0
        norm_w = {k: v / total_w for k, v in active.items()}

        score = sum(components[k] * norm_w[k] for k in metrics) * 100.0
        # 의미 유사도가 없을 때만 전체 점수에 본문 누락 패널티를 건다.
        # 있을 때는 embedding.py가 semantic 점수에만 패널티를 적용했다.
        if semantic_value is None and not doc.has_body:
            score *= cfg.no_body_penalty
        if off_topic:
            score = 0.0

        doc.score = round(score, 1)
        doc.score_detail = {
            "metrics_used": list(metrics),
            "weights_used": {k: round(norm_w[k], 3) for k in metrics},
            "components": {k: round(components[k], 3) for k in metrics},
            "contributions": {
                k: round(components[k] * norm_w[k] * 100, 1) for k in metrics
            },
            "keyword_detail": kw_detail,
            "topic_evidence": gate_evidence,
            "source_tier": tier_name,
            "recency_detail": rec_detail,
            "spread_detail": spr_detail,
            "semantic": (
                round(semantic_value, 3)
                if semantic_value is not None
                else "임베딩 미적용"
            ),
            "no_body_penalty": semantic_value is None and not doc.has_body,
            "off_topic": off_topic,
        }

    documents.sort(
        key=lambda d: (
            -(d.score or 0.0),
            -(d.published_at.timestamp() if d.published_at else 0),
        )
    )
    return documents

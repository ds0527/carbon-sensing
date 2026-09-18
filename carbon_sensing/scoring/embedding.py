"""의미 유사도 스코어링. 주제 설명문과 문서를 임베딩해 코사인 유사도를 낸다."""

from __future__ import annotations

import hashlib
import logging

from ..config import get_app_config, get_topics
from ..models import Document
from ..storage.db import Database

log = logging.getLogger(__name__)

# 문서 임베딩에 넣는 텍스트 길이. 제목+본문 앞부분이면 주제 판별에 충분하다.
EMBED_TEXT_LIMIT = 1200
# 한 번에 보내는 임베딩 배치 크기.
BATCH_SIZE = 64


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def topic_text(extra_topics: list[str] | None = None) -> str:
    """주제 설명문. 임베딩 비교의 기준이 된다."""
    topics = get_topics()
    parts = [
        topics.base_topic,
        "철강산업의 탄소중립과 탈탄소 전환에 관한 동향.",
        "수소환원제철, 전기로 전환, 고로 감산, 탄소배출권, CBAM 등 탄소국경조치,",
        "저탄소 철강 기술 개발과 관련 설비 투자, 정책과 규제, 시장 영향을 포함한다.",
    ]
    if extra_topics:
        parts.append("특히 다음 주제에 주목한다: " + ", ".join(extra_topics))
    return " ".join(parts)


def embed_input(doc: Document) -> str:
    """문서 임베딩 입력. 본문이 없으면 제목+발췌문만 들어간다."""
    body = (doc.body or doc.snippet or "").strip()
    text = f"{doc.title}\n{body}"
    return text[:EMBED_TEXT_LIMIT]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def semantic_scores(
    documents: list[Document],
    client,
    db: Database | None = None,
    extra_topics: list[str] | None = None,
) -> dict[str, float] | None:
    """문서 id -> 의미 유사도(0~1). 임베딩을 못 구하면 None.

    본문 추출에 실패한 문서는 판단 근거가 제목·발췌문뿐이므로
    config.yaml의 no_body_penalty(기본 0.85)를 이 점수에만 적용한다.
    """
    if client is None or not documents:
        return None

    model = client.embedding_model
    penalty = get_app_config().scoring.no_body_penalty

    # 기준 벡터(주제 설명문)
    topic_input = topic_text(extra_topics)
    topic_vec = _cached(db, topic_input, model)
    if topic_vec is None:
        vectors = await client.embed([topic_input])
        if not vectors:
            log.warning("주제 임베딩을 얻지 못해 의미 유사도를 건너뜁니다.")
            return None
        topic_vec = vectors[0]
        _store(db, model, [(text_hash(topic_input), topic_vec)])

    # 문서 벡터: 캐시에 없는 것만 배치로 요청한다.
    inputs = {doc.id: embed_input(doc) for doc in documents}
    doc_vectors: dict[str, list[float]] = {}
    pending: list[tuple[str, str]] = []  # (doc_id, text)
    for doc_id, text in inputs.items():
        cached = _cached(db, text, model)
        if cached is not None:
            doc_vectors[doc_id] = cached
        else:
            pending.append((doc_id, text))

    log.info(
        "임베딩: 캐시 %d건, 신규 %d건", len(doc_vectors), len(pending)
    )
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        vectors = await client.embed([text for _, text in batch])
        if not vectors:
            log.warning("임베딩 배치 실패, 해당 문서는 의미 점수 없이 진행합니다.")
            continue
        to_store: list[tuple[str, list[float]]] = []
        for (doc_id, text), vector in zip(batch, vectors):
            doc_vectors[doc_id] = vector
            to_store.append((text_hash(text), vector))
        _store(db, model, to_store)

    if not doc_vectors:
        return None

    scores: dict[str, float] = {}
    for doc in documents:
        vector = doc_vectors.get(doc.id)
        if vector is None:
            continue
        # 임베딩 코사인은 보통 양수 구간에 모인다. 음수는 무관으로 보고 0으로 자른다.
        value = max(0.0, cosine(topic_vec, vector))
        if not doc.has_body:
            value *= penalty
        scores[doc.id] = value
    return scores


def _cached(db: Database | None, text: str, model: str) -> list[float] | None:
    if db is None:
        return None
    return db.get_embedding(text_hash(text), model)


def _store(
    db: Database | None, model: str, items: list[tuple[str, list[float]]]
) -> None:
    if db is None or not items:
        return
    db.save_embeddings(model, items)

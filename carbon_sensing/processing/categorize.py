"""문서 카테고리 분류(설비/정책/기술/시장/기타). 규칙 기반이라 LLM 비용이 없다."""

from __future__ import annotations

from ..config import get_topics
from ..models import Document
from ..scoring.matching import match_keywords, normalize

CATEGORIES = ["정책", "설비", "기술", "시장"]
DEFAULT_CATEGORY = "기타"

# 동률일 때 우선순위. 정책 발표가 기술 용어를 함께 언급하는 경우가 많아
# 정책을 앞에 둔다.
PRIORITY = ["정책", "설비", "기술", "시장"]


def assign_category(doc: Document) -> str:
    """제목 x2, 본문 x1 가중치로 카테고리별 히트를 세어 가장 높은 쪽을 고른다."""
    keywords_by_category = get_topics().category_keywords
    if not keywords_by_category:
        return DEFAULT_CATEGORY

    title = normalize(doc.title)
    body = normalize(doc.searchable_text())

    scores: dict[str, float] = {}
    for category in CATEGORIES:
        keywords = keywords_by_category.get(category, [])
        if not keywords:
            continue
        title_hits, _ = match_keywords(title, keywords, strict=False)
        body_hits, _ = match_keywords(body, keywords, strict=False)
        scores[category] = title_hits * 2.0 + body_hits * 1.0

    best_score = max(scores.values(), default=0.0)
    if best_score <= 0:
        return DEFAULT_CATEGORY

    tied = [c for c, s in scores.items() if s == best_score]
    for category in PRIORITY:
        if category in tied:
            return category
    return tied[0]


def categorize_documents(documents: list[Document]) -> None:
    """문서 리스트에 category를 채운다(제자리 수정)."""
    for doc in documents:
        doc.category = assign_category(doc)

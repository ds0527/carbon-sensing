"""OpenAlex 논문 수집기. 무료이고 API 키가 필요 없다."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ..config import get_app_config, get_settings
from ..daterange import DateRange, KST
from ..models import Document
from .base import BaseCollector, clean_text

ENDPOINT = "https://api.openalex.org/works"
MAX_PER_PAGE = 50


class OpenAlexCollector(BaseCollector):
    """영문 질의어만 보낸다. 논문 검색에 한글 질의어는 거의 안 걸린다."""

    name = "openalex"

    async def _collect(
        self, queries: list[str], period: DateRange
    ) -> tuple[list[Document], list[str]]:
        from ..queries import split_language

        _ko, en = split_language(queries)
        targets = en or queries
        limit = min(get_app_config().collect.per_query_limit, MAX_PER_PAGE)
        warnings: list[str] = []

        results = await asyncio.gather(
            *(self._search(q, limit, period) for q in targets),
            return_exceptions=True,
        )
        docs: list[Document] = []
        for query, result in zip(targets, results):
            if isinstance(result, BaseException):
                warnings.append(f"질의어 '{query}' 실패: {result}")
                continue
            docs.extend(result)
        return docs, warnings

    async def _search(
        self, query: str, limit: int, period: DateRange
    ) -> list[Document]:
        start = period.start.astimezone(KST).date().isoformat()
        end = period.end.astimezone(KST).date().isoformat()
        settings = get_settings()
        resp = await self.fetcher.get(
            ENDPOINT,
            params={
                "search": query,
                "filter": f"from_publication_date:{start},to_publication_date:{end}",
                "per-page": limit,
                # OpenAlex는 연락처를 주면 우선 처리해준다(polite pool).
                "mailto": _mailto(settings.user_agent),
            },
        )
        resp.raise_for_status()
        items = resp.json().get("results", []) or []
        docs: list[Document] = []
        for item in items:
            doc = self._to_document(item)
            if doc is None:
                continue
            if doc.published_at is not None and not period.contains(doc.published_at):
                continue
            docs.append(doc)
        return docs

    def _to_document(self, item: dict) -> Document | None:
        title = clean_text(item.get("display_name"))
        if not title:
            return None
        doi = item.get("doi")
        url = doi or item.get("id") or ""
        landing = (item.get("primary_location") or {}).get("landing_page_url")
        url = doi or landing or url
        if not url:
            return None
        source = (
            ((item.get("primary_location") or {}).get("source") or {}).get(
                "display_name"
            )
            or "OpenAlex"
        )
        return Document(
            title=title,
            url=url,
            source_name=clean_text(source) or "OpenAlex",
            published_at=_parse_date(item.get("publication_date")),
            language=item.get("language") or "en",
            region="해외",
            doc_type="논문",
            snippet=_abstract(item),
        )


def _mailto(user_agent: str) -> str:
    """User-Agent에 적힌 이메일을 꺼내 polite pool에 쓴다."""
    for token in user_agent.replace(")", " ").replace(";", " ").split():
        if "@" in token and "." in token:
            return token
    return "carbon-sensing@example.com"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _abstract(item: dict) -> str:
    """OpenAlex는 초록을 단어 위치 역색인으로 준다. 원문 순서로 복원한다."""
    inverted = item.get("abstract_inverted_index")
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indices in inverted.items():
        if not isinstance(indices, list):
            continue
        for idx in indices:
            if isinstance(idx, int):
                positions.append((idx, word))
    positions.sort()
    return clean_text(" ".join(word for _, word in positions))[:1500]

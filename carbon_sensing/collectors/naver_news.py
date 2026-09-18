"""네이버 검색 API(뉴스) 수집기. 무료, 일 25,000회."""

from __future__ import annotations

import asyncio
from datetime import datetime
from email.utils import parsedate_to_datetime

from ..config import get_app_config, get_settings
from ..daterange import DateRange
from ..models import Document
from .base import BaseCollector, clean_text, guess_region, source_name_from_url

ENDPOINT = "https://openapi.naver.com/v1/search/news.json"
PAGE_SIZE = 100          # API 상한
MAX_START = 1000         # start 파라미터 상한


class NaverNewsCollector(BaseCollector):
    name = "naver_news"

    def available(self) -> tuple[bool, str | None]:
        settings = get_settings()
        if not settings.naver_ready:
            missing = settings.missing_keys().get("naver_news", [])
            return False, f".env에 {', '.join(missing)}가 없습니다."
        return True, None

    async def _collect(
        self, queries: list[str], period: DateRange
    ) -> tuple[list[Document], list[str]]:
        settings = get_settings()
        limit = get_app_config().collect.per_query_limit
        headers = {
            "X-Naver-Client-Id": settings.naver_client_id or "",
            "X-Naver-Client-Secret": settings.naver_client_secret or "",
        }
        warnings: list[str] = []

        results = await asyncio.gather(
            *(self._search(q, limit, headers, period) for q in queries),
            return_exceptions=True,
        )
        docs: list[Document] = []
        for query, result in zip(queries, results):
            if isinstance(result, BaseException):
                warnings.append(f"질의어 '{query}' 실패: {result}")
                continue
            docs.extend(result)
        return docs, warnings

    async def _search(
        self,
        query: str,
        limit: int,
        headers: dict[str, str],
        period: DateRange,
    ) -> list[Document]:
        docs: list[Document] = []
        start = 1
        while len(docs) < limit and start <= MAX_START:
            display = min(PAGE_SIZE, limit - len(docs))
            resp = await self.fetcher.get(
                ENDPOINT,
                params={
                    "query": query,
                    "display": display,
                    "start": start,
                    "sort": "date",  # 기간 필터가 없으므로 최신순으로 받아 자른다
                },
                headers=headers,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            if not items:
                break
            older_than_period = False
            for item in items:
                doc = self._to_document(item, query)
                if doc is None:
                    continue
                if doc.published_at is not None and doc.published_at < period.start:
                    older_than_period = True
                    continue
                if doc.published_at is not None and doc.published_at > period.end:
                    continue
                docs.append(doc)
            if older_than_period:
                # 최신순 정렬이므로 기간보다 오래된 결과가 나오면 더 볼 필요가 없다.
                break
            start += display
        return docs

    def _to_document(self, item: dict, query: str) -> Document | None:
        url = item.get("originallink") or item.get("link") or ""
        if not url:
            return None
        title = clean_text(item.get("title"))
        if not title:
            return None
        published = _parse_pubdate(item.get("pubDate"))
        return Document(
            title=title,
            url=url,
            source_name=source_name_from_url(url),
            published_at=published,
            language="ko",
            region=guess_region(url, "ko"),
            doc_type="기사",
            snippet=clean_text(item.get("description")),
            notes=[] if published else ["게재일 파싱 실패"],
        )


def _parse_pubdate(value: str | None) -> datetime | None:
    """네이버는 RFC 1123 형식(Mon, 15 Sep 2026 09:00:00 +0900)을 준다."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    from ..daterange import to_utc

    return to_utc(parsed)

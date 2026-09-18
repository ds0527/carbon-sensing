"""Tavily 검색 API 수집기. 국내외 범용. 키가 없으면 조용히 건너뛴다."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ..config import get_app_config, get_settings
from ..daterange import DateRange, to_utc
from ..models import Document
from .base import BaseCollector, clean_text, guess_region, source_name_from_url

ENDPOINT = "https://api.tavily.com/search"
MAX_RESULTS_PER_CALL = 20  # Tavily 상한


class WebSearchCollector(BaseCollector):
    name = "web_search"

    def available(self) -> tuple[bool, str | None]:
        if not get_settings().tavily_ready:
            return False, ".env에 TAVILY_API_KEY가 없습니다."
        return True, None

    async def _collect(
        self, queries: list[str], period: DateRange
    ) -> tuple[list[Document], list[str]]:
        settings = get_settings()
        limit = min(get_app_config().collect.per_query_limit, MAX_RESULTS_PER_CALL)
        warnings: list[str] = []

        results = await asyncio.gather(
            *(
                self._search(q, limit, settings.tavily_api_key or "", period)
                for q in queries
            ),
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
        self, query: str, limit: int, api_key: str, period: DateRange
    ) -> list[Document]:
        # 기간이 긴 경우에도 days로 넉넉히 받아서 로컬에서 정확히 자른다.
        days = max(1, (datetime.now(timezone.utc) - period.start).days + 1)
        payload = {
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
            "topic": "news",
            "days": days,
            "include_answer": False,
            "include_raw_content": False,
        }
        resp = await self.fetcher.post(
            ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
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
        url = item.get("url") or ""
        title = clean_text(item.get("title"))
        if not url or not title:
            return None
        published = _parse_iso(item.get("published_date"))
        language = "ko" if _has_hangul(title) else "en"
        return Document(
            title=title,
            url=url,
            source_name=source_name_from_url(url),
            published_at=published,
            language=language,
            region=guess_region(url, language),
            doc_type="기사",
            snippet=clean_text(item.get("content")),
            notes=[] if published else ["게재일 파싱 실패"],
        )


def _has_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return to_utc(datetime.fromisoformat(text))
    except ValueError:
        pass
    # Tavily가 RFC 1123으로 줄 때도 있다.
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return to_utc(parsed) if parsed else None

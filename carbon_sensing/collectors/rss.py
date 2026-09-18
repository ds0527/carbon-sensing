"""sources.yaml의 기관·언론 피드를 읽는 수집기. 키가 필요 없다."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import feedparser

from ..config import get_sources
from ..daterange import DateRange, to_utc
from ..models import Document
from .base import BaseCollector, clean_text, guess_region, source_name_from_url


class RssCollector(BaseCollector):
    """피드는 질의어로 검색할 수 없으므로 전량을 받아 기간과 키워드로 거른다."""

    name = "rss"

    def available(self) -> tuple[bool, str | None]:
        if not get_sources().feeds:
            return False, "sources.yaml에 feeds 항목이 없습니다."
        return True, None

    async def _collect(
        self, queries: list[str], period: DateRange
    ) -> tuple[list[Document], list[str]]:
        feeds = get_sources().feeds
        warnings: list[str] = []
        results = await asyncio.gather(
            *(self._fetch_feed(feed, period) for feed in feeds),
            return_exceptions=True,
        )
        docs: list[Document] = []
        for feed, result in zip(feeds, results):
            if isinstance(result, BaseException):
                warnings.append(f"피드 '{feed.get('name', feed.get('url'))}' 실패: {result}")
                continue
            docs.extend(result)
        return docs, warnings

    async def _fetch_feed(
        self, feed: dict[str, Any], period: DateRange
    ) -> list[Document]:
        url = feed.get("url")
        if not url:
            return []
        resp = await self.fetcher.get(url)
        resp.raise_for_status()
        # feedparser는 동기 라이브러리이므로 이벤트 루프를 막지 않게 스레드로 넘긴다.
        parsed = await asyncio.to_thread(feedparser.parse, resp.content)
        docs: list[Document] = []
        for entry in parsed.entries:
            doc = self._to_document(entry, feed)
            if doc is None:
                continue
            if doc.published_at is not None and not period.contains(doc.published_at):
                continue
            docs.append(doc)
        return docs

    def _to_document(self, entry: Any, feed: dict[str, Any]) -> Document | None:
        link = getattr(entry, "link", "") or ""
        title = clean_text(getattr(entry, "title", ""))
        if not link or not title:
            return None
        published = _entry_datetime(entry)
        language = feed.get("language") or "ko"
        summary = clean_text(
            getattr(entry, "summary", None) or getattr(entry, "description", None)
        )
        return Document(
            title=title,
            url=link,
            source_name=feed.get("name") or source_name_from_url(link),
            published_at=published,
            language=language,
            region=feed.get("region") or guess_region(link, language),
            doc_type=feed.get("doc_type") or "기사",
            snippet=summary,
            notes=[] if published else ["게재일 파싱 실패"],
        )


def _entry_datetime(entry: Any) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        struct = getattr(entry, attr, None)
        if struct:
            try:
                # feedparser의 *_parsed는 UTC 기준 time.struct_time이다.
                return datetime(*struct[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return to_utc(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
            except ValueError:
                continue
    return None

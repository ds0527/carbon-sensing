"""GDELT 2.0 Doc API 수집기. 해외 뉴스 대량 수집용. 키가 필요 없다."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ..config import get_app_config
from ..daterange import DateRange
from ..models import Document
from .base import BaseCollector, clean_text

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 75  # API 상한
# GDELT는 최근 3개월치만 제공한다.
LOOKBACK_LIMIT_DAYS = 90


class GdeltCollector(BaseCollector):
    """영문 질의어만 보낸다. 한글 질의어는 GDELT 색인 커버리지가 낮다."""

    name = "gdelt"

    async def _collect(
        self, queries: list[str], period: DateRange
    ) -> tuple[list[Document], list[str]]:
        from ..queries import split_language

        _ko, en = split_language(queries)
        targets = en or queries
        limit = min(get_app_config().collect.per_query_limit, MAX_RECORDS)
        warnings: list[str] = []

        age_days = (datetime.now(timezone.utc) - period.start).days
        if age_days > LOOKBACK_LIMIT_DAYS:
            warnings.append(
                f"기간 시작일이 {age_days}일 전이라 GDELT 커버리지"
                f"({LOOKBACK_LIMIT_DAYS}일)를 넘습니다. 일부만 수집됩니다."
            )

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
        resp = await self.fetcher.get(
            ENDPOINT,
            params={
                # 구문 검색으로 묶어야 관련 없는 결과가 줄어든다.
                "query": f'"{query}" sourcelang:english',
                "mode": "ArtList",
                "format": "json",
                "maxrecords": limit,
                "sort": "DateDesc",
                "startdatetime": period.start.strftime("%Y%m%d%H%M%S"),
                "enddatetime": period.end.strftime("%Y%m%d%H%M%S"),
            },
        )
        resp.raise_for_status()
        # GDELT는 오류 시에도 200과 함께 HTML·빈 본문을 주는 경우가 있다.
        try:
            payload = resp.json()
        except ValueError:
            return []
        items = payload.get("articles", []) or []
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
        domain = (item.get("domain") or "").lower()
        published = _parse_seendate(item.get("seendate"))
        return Document(
            title=title,
            url=url,
            source_name=domain or "gdelt",
            published_at=published,
            language=item.get("language") or "en",
            region="해외",
            doc_type="기사",
            snippet="",  # GDELT는 발췌문을 주지 않는다. 본문 추출로 채운다.
            notes=[] if published else ["게재일 파싱 실패"],
        )


def _parse_seendate(value: str | None) -> datetime | None:
    """GDELT seendate 형식: 20260915T123000Z."""
    if not value:
        return None
    text = str(value).strip().replace("T", "").replace("Z", "")
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None

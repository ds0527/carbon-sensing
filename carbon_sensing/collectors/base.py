"""수집기 공통 인터페이스. 모든 어댑터가 같은 시그니처와 반환형을 쓴다."""

from __future__ import annotations

import html
import logging
import re
from abc import ABC, abstractmethod
from urllib.parse import urlparse

from ..daterange import DateRange
from ..http import HttpFetcher
from ..models import CollectorResult, Document

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(value: str | None) -> str:
    """검색 API가 섞어 보내는 HTML 태그와 엔티티를 벗긴다."""
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def guess_region(url: str, language: str | None = None) -> str:
    """도메인과 언어로 국내/해외를 나눈다."""
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".kr") or host.endswith(".korea.kr"):
        return "국내"
    if language and language.startswith("ko"):
        return "국내"
    return "해외"


def source_name_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or "출처미상"


class BaseCollector(ABC):
    """수집기 한 개.

    - name: 리포트와 UI에 그대로 표시되는 식별자
    - available(): .env 키가 준비됐는지. False면 조용히 건너뛴다.
    - collect(): 실패해도 예외를 위로 던지지 않고 CollectorResult에 담는다.
    """

    name: str = "base"

    def __init__(self, fetcher: HttpFetcher) -> None:
        self.fetcher = fetcher

    def available(self) -> tuple[bool, str | None]:
        return True, None

    @abstractmethod
    async def _collect(
        self, queries: list[str], period: DateRange
    ) -> tuple[list[Document], list[str]]:
        """(문서 목록, 경고 목록)을 반환한다."""

    async def collect(self, queries: list[str], period: DateRange) -> CollectorResult:
        ok, reason = self.available()
        if not ok:
            log.info("%s 건너뜀: %s", self.name, reason)
            return CollectorResult(
                collector=self.name, ok=True, skipped_reason=reason
            )
        try:
            docs, warnings = await self._collect(queries, period)
        except Exception as exc:  # 한 수집기 실패가 전체를 멈추지 않는다.
            log.warning("%s 수집 실패: %s", self.name, exc, exc_info=True)
            return CollectorResult(
                collector=self.name, ok=False, errors=[f"{type(exc).__name__}: {exc}"]
            )
        for doc in docs:
            doc.collector = self.name
        return CollectorResult(collector=self.name, documents=docs, errors=warnings)

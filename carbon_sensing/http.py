"""공용 HTTP 계층: 도메인당 속도 제한, robots.txt 준수, 재시도."""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.robotparser as robotparser
from collections import defaultdict
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_app_config, get_settings

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RateLimitedError(Exception):
    """재시도할 가치가 있는 HTTP 응답."""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"HTTP {status_code}: {url}")
        self.status_code = status_code


def _should_retry(exc: BaseException) -> bool:
    return isinstance(
        exc, (RateLimitedError, httpx.TimeoutException, httpx.TransportError)
    )


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


class DomainRateLimiter:
    """도메인당 최소 간격을 강제한다. 비동기 병렬 호출에서도 순서가 지켜진다."""

    def __init__(self, delay_seconds: float) -> None:
        self.delay = max(0.0, delay_seconds)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_call: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        if self.delay <= 0:
            return
        host = host_of(url) or "-"
        async with self._locks[host]:
            last = self._last_call.get(host)
            now = time.monotonic()
            if last is not None:
                gap = self.delay - (now - last)
                if gap > 0:
                    await asyncio.sleep(gap)
            self._last_call[host] = time.monotonic()


class RobotsCache:
    """도메인별 robots.txt를 한 번만 받아 캐시한다."""

    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._parsers: dict[str, robotparser.RobotFileParser | None] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        if not host:
            return False
        async with self._locks[host]:
            if host not in self._parsers:
                self._parsers[host] = await self._load(client, url)
        parser = self._parsers[host]
        if parser is None:
            # robots.txt를 읽지 못한 경우는 차단으로 보지 않는다(규격상 허용).
            return True
        return parser.can_fetch(self.user_agent, url)

    async def _load(
        self, client: httpx.AsyncClient, url: str
    ) -> robotparser.RobotFileParser | None:
        robots_url = urljoin(url, "/robots.txt")
        try:
            resp = await client.get(robots_url, timeout=10.0)
        except httpx.HTTPError as exc:
            log.debug("robots.txt 조회 실패 %s: %s", robots_url, exc)
            return None
        if resp.status_code >= 400:
            return None
        parser = robotparser.RobotFileParser()
        try:
            parser.parse(resp.text.splitlines())
        except Exception as exc:  # robots.txt 형식이 깨진 사이트
            log.debug("robots.txt 파싱 실패 %s: %s", robots_url, exc)
            return None
        return parser


class HttpFetcher:
    """수집기와 본문 추출기가 공유하는 요청기."""

    def __init__(self) -> None:
        cfg = get_app_config().collect
        settings = get_settings()
        self.cfg = cfg
        self.user_agent = settings.user_agent
        self.limiter = DomainRateLimiter(cfg.domain_delay_seconds)
        self.robots = RobotsCache(self.user_agent)
        self.semaphore = asyncio.Semaphore(cfg.concurrency)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "HttpFetcher":
        self._client = httpx.AsyncClient(
            timeout=self.cfg.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent},
        )
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HttpFetcher를 async with 블록 안에서 사용하세요.")
        return self._client

    async def request(
        self,
        method: str,
        url: str,
        *,
        check_robots: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        """재시도는 tenacity가, 간격은 limiter가, 동시성은 semaphore가 맡는다."""

        @retry(
            retry=retry_if_exception(_should_retry),
            stop=stop_after_attempt(self.cfg.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            reraise=True,
        )
        async def _do() -> httpx.Response:
            async with self.semaphore:
                await self.limiter.wait(url)
                resp = await self.client.request(method, url, **kwargs)
            if resp.status_code in RETRY_STATUS:
                raise RateLimitedError(resp.status_code, url)
            return resp

        if check_robots and self.cfg.respect_robots:
            if not await self.robots.allowed(self.client, url):
                raise PermissionError(f"robots.txt가 수집을 허용하지 않음: {url}")
        return await _do()

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

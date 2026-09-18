"""본문 추출. robots.txt를 확인하고 trafilatura로 본문만 남긴다."""

from __future__ import annotations

import asyncio
import logging

import trafilatura

from ..http import HttpFetcher
from ..models import Document
from ..storage.db import Database

log = logging.getLogger(__name__)

MAX_HTML_BYTES = 4_000_000  # 과도하게 큰 페이지는 건너뛴다


async def fetch_bodies(
    documents: list[Document],
    fetcher: HttpFetcher,
    db: Database | None = None,
    limit: int | None = None,
) -> None:
    """상위 문서의 본문을 채운다. 실패해도 body=None으로 두고 계속 진행한다."""
    targets = documents if limit is None else documents[:limit]
    await asyncio.gather(*(_fill_one(doc, fetcher, db) for doc in targets))


async def _fill_one(doc: Document, fetcher: HttpFetcher, db: Database | None) -> None:
    if doc.has_body:
        return
    if db is not None:
        cached = db.get_body(doc.url)
        if cached:
            doc.body = cached
            return
    try:
        resp = await fetcher.get(doc.url, check_robots=True)
    except PermissionError as exc:
        doc.notes.append("robots.txt 차단")
        log.debug("%s", exc)
        return
    except Exception as exc:
        doc.notes.append(f"본문 요청 실패({type(exc).__name__})")
        log.debug("본문 요청 실패 %s: %s", doc.url, exc)
        return

    if resp.status_code >= 400:
        doc.notes.append(f"본문 요청 실패(HTTP {resp.status_code})")
        return
    if len(resp.content) > MAX_HTML_BYTES:
        doc.notes.append("본문 과대(추출 생략)")
        return

    try:
        body = await asyncio.to_thread(
            trafilatura.extract,
            resp.text,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
    except Exception as exc:
        doc.notes.append(f"본문 추출 실패({type(exc).__name__})")
        log.debug("본문 추출 실패 %s: %s", doc.url, exc)
        return

    if not body or not body.strip():
        doc.notes.append("본문 추출 실패(내용 없음)")
        return

    doc.body = body.strip()
    if db is not None:
        db.save_body(doc.url, doc.body)

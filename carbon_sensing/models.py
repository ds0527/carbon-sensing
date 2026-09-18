"""파이프라인 전체가 주고받는 데이터 구조."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Region = Literal["국내", "해외"]
DocType = Literal["기사", "보도자료", "보고서", "논문"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Document:
    """수집기가 반환하는 단일 문서. 모든 수집기가 이 형식을 지킨다."""

    title: str
    url: str
    source_name: str
    published_at: datetime | None
    language: str
    region: Region
    doc_type: DocType
    snippet: str
    body: str | None = None
    category: str = "기타"
    collector: str = ""
    collected_at: datetime = field(default_factory=utcnow)
    score: float | None = None
    score_detail: dict[str, Any] = field(default_factory=dict)
    duplicate_count: int = 1
    id: str = ""
    # 중복 병합으로 흡수된 문서들의 (언론사, URL). 3단계에서 받침 링크로 쓴다.
    merged_from: list[tuple[str, str]] = field(default_factory=list)
    # 본문 추출 실패 사유 등 파이프라인이 남기는 메모.
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = make_doc_id(self.url)

    @property
    def date_unknown(self) -> bool:
        return self.published_at is None

    @property
    def has_body(self) -> bool:
        return bool(self.body and self.body.strip())

    def searchable_text(self) -> str:
        """스코어링이 보는 본문. 본문이 없으면 발췌문으로 대체한다."""
        return (self.body or self.snippet or "").strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source_name": self.source_name,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "language": self.language,
            "region": self.region,
            "doc_type": self.doc_type,
            "category": self.category,
            "snippet": self.snippet,
            "body_chars": len(self.body) if self.body else 0,
            "collector": self.collector,
            "collected_at": self.collected_at.isoformat(),
            "score": self.score,
            "score_detail": self.score_detail,
            "duplicate_count": self.duplicate_count,
            "merged_from": [list(x) for x in self.merged_from],
            "notes": self.notes,
        }


def make_doc_id(url: str) -> str:
    from .processing.dedupe import normalize_url

    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:16]


@dataclass
class CollectorResult:
    """수집기 한 개의 실행 결과. 실패해도 파이프라인은 계속 진행한다."""

    collector: str
    documents: list[Document] = field(default_factory=list)
    ok: bool = True
    skipped_reason: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.skipped_reason:
            return "건너뜀"
        if not self.ok:
            return "실패"
        if self.errors:
            return "부분 성공"
        return "성공"


@dataclass
class RunContext:
    """한 번의 실행 조건. 리포트 재현에 필요한 값을 모두 담는다."""

    date_from: datetime
    date_to: datetime
    base_topic: str
    extra_topics: list[str]
    mode: str
    excludes: list[str]
    queries: list[str]
    top_n: int
    started_at: datetime = field(default_factory=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "base_topic": self.base_topic,
            "extra_topics": self.extra_topics,
            "mode": self.mode,
            "excludes": self.excludes,
            "queries": self.queries,
            "top_n": self.top_n,
            "started_at": self.started_at.isoformat(),
        }

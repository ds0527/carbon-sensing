"""LLM 호출 4개 지점. 근거 검증까지 여기서 끝낸다."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..config import get_app_config, get_topics
from ..models import Document
from . import prompts
from .client import LlmClient
from .schemas import (
    ArticleSummary,
    Claim,
    QueryExpansion,
    RepresentativeSelection,
    SynthesisResult,
)

log = logging.getLogger(__name__)

UNVERIFIED = "원문 미확인"


@dataclass
class GroundedText:
    """검증을 통과한 문장 하나."""

    text: str
    source_doc_id: str | None
    verified: bool = True

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "source_doc_id": self.source_doc_id,
            "verified": self.verified,
        }


@dataclass
class DocumentInsight:
    """기사 한 건의 요약·시사점."""

    doc_id: str
    summary: list[GroundedText] = field(default_factory=list)
    implications: list[GroundedText] = field(default_factory=list)
    selection_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "summary": [g.to_dict() for g in self.summary],
            "implications": [g.to_dict() for g in self.implications],
            "selection_reason": self.selection_reason,
        }


@dataclass
class Synthesis:
    """기간 전체 종합."""

    headline: str
    trends: list[GroundedText] = field(default_factory=list)
    implications: list[GroundedText] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "headline": self.headline,
            "trends": [g.to_dict() for g in self.trends],
            "implications": [g.to_dict() for g in self.implications],
        }


def _ground(claims: list[Claim], valid_ids: set[str]) -> list[GroundedText]:
    """LLM이 준 근거 id가 실제 문서인지 확인한다.

    없는 id를 만들어냈거나 unknown이면 문장을 버리지 않고 '원문 미확인'
    표시를 남긴다. 조용히 지우면 사용자가 문제를 못 보게 된다.
    """
    out: list[GroundedText] = []
    for claim in claims:
        text = (claim.text or "").strip()
        if not text:
            continue
        doc_id = (claim.source_doc_id or "").strip()
        if doc_id in valid_ids:
            out.append(GroundedText(text=text, source_doc_id=doc_id, verified=True))
        else:
            if doc_id and doc_id.lower() != "unknown":
                log.warning("LLM이 존재하지 않는 문서 id를 참조: %s", doc_id)
            out.append(
                GroundedText(
                    text=f"{text} ({UNVERIFIED})", source_doc_id=None, verified=False
                )
            )
    return out


async def expand_queries(
    client: LlmClient, extra_topics: list[str]
) -> list[str]:
    """추가 주제를 검색 질의어로 확장한다. 실패하면 빈 리스트."""
    if not extra_topics:
        return []
    result = await client.structured(
        prompts.SYSTEM,
        prompts.expand_queries_user(extra_topics, get_topics().base_topic),
        QueryExpansion,
        model=client.model_light,
        temperature=0.2,
    )
    if result is None:
        log.info("질의어 확장 실패, 규칙 기반 질의어만 사용합니다.")
        return []
    cleaned = [q.strip() for q in result.queries if q and q.strip()]
    return cleaned[:5]


async def select_representative(
    client: LlmClient, candidates: list[Document], top_n: int
) -> dict[str, str]:
    """대표 기사 doc_id -> 선정 사유. 실패하면 빈 dict."""
    if not candidates:
        return {}
    body_limit = get_app_config().report.body_char_limit
    result = await client.structured(
        prompts.SYSTEM,
        prompts.select_representative_user(candidates, top_n, body_limit),
        RepresentativeSelection,
        model=client.model,
        temperature=0.2,
    )
    if result is None:
        return {}
    valid = {d.id for d in candidates}
    picks: dict[str, str] = {}
    for pick in result.picks:
        doc_id = (pick.doc_id or "").strip()
        if doc_id not in valid:
            log.warning("대표 기사 선정에서 알 수 없는 id 무시: %s", doc_id)
            continue
        if doc_id in picks:
            continue
        picks[doc_id] = (pick.reason or "").strip()
        if len(picks) >= top_n:
            break
    return picks


async def summarize_document(
    client: LlmClient, doc: Document
) -> DocumentInsight | None:
    """기사 한 건의 요약 3~5문장 + 시사점 2~3개."""
    body_limit = get_app_config().report.body_char_limit
    result = await client.structured(
        prompts.SYSTEM,
        prompts.summarize_user(doc, body_limit),
        ArticleSummary,
        model=client.model,
        temperature=0.2,
    )
    if result is None:
        return None
    valid = {doc.id}
    return DocumentInsight(
        doc_id=doc.id,
        summary=_ground(result.summary, valid),
        implications=_ground(result.implications, valid),
    )


async def summarize_documents(
    client: LlmClient, docs: list[Document]
) -> dict[str, DocumentInsight]:
    """대표 기사들을 병렬로 요약한다. 일부 실패는 건너뛴다."""
    results = await asyncio.gather(
        *(summarize_document(client, doc) for doc in docs),
        return_exceptions=True,
    )
    insights: dict[str, DocumentInsight] = {}
    for doc, result in zip(docs, results):
        if isinstance(result, BaseException):
            log.warning("요약 실패(%s): %s", doc.id, result)
            continue
        if result is None:
            log.warning("요약 실패(%s): 응답 없음", doc.id)
            continue
        insights[doc.id] = result
    return insights


async def synthesize(
    client: LlmClient,
    docs: list[Document],
    insights: dict[str, DocumentInsight],
    period_label: str,
    category_counts: dict[str, int],
) -> Synthesis | None:
    """기간 전체 종합: 헤드라인 1문장, 트렌드 3~5개, 시사점 3개."""
    if not docs:
        return None
    payload = []
    for doc in docs:
        insight = insights.get(doc.id)
        summary_lines = [g.text for g in insight.summary] if insight else [doc.snippet]
        implication_lines = [g.text for g in insight.implications] if insight else []
        payload.append((doc, summary_lines, implication_lines))

    result = await client.structured(
        prompts.SYSTEM,
        prompts.synthesize_user(payload, period_label, category_counts),
        SynthesisResult,
        model=client.model,
        temperature=0.4,  # 시사점은 요약보다 약간 높게
    )
    if result is None:
        return None
    valid = {d.id for d in docs}
    return Synthesis(
        headline=(result.headline or "").strip(),
        trends=_ground(result.trends, valid),
        implications=_ground(result.implications, valid),
    )

"""수집 → 정제 → 분류 → 스코어링 → LLM 요약 → 출력.

CLI와 3단계 UI가 함께 호출하는 단일 경로. OPENAI_API_KEY가 없으면
LLM·임베딩 단계만 건너뛰고 1단계와 동일하게 목록까지 만든다.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from pathlib import Path
from typing import Callable

from .collectors import build_collectors
from .config import get_app_config, get_topics
from .daterange import DateRange
from .http import HttpFetcher
from .llm import TokenBudgetExceeded, build_client
from .llm import service as llm_service
from .models import CollectorResult, Document, RunContext
from .processing.categorize import categorize_documents
from .processing.dedupe import dedupe
from .processing.extract import fetch_bodies
from .queries import build_queries
from .report import RunResult, make_output_dir, write_outputs
from .scoring import score_documents
from .scoring.embedding import semantic_scores
from .storage import Database

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, str], None]

NO_KEY_REASON = "LLM 단계를 건너뛰었습니다 - OPENAI_API_KEY 미설정 또는 클라이언트 생성 실패."
NO_LLM_FLAG_REASON = "LLM 단계를 건너뛰었습니다 - --no-llm 옵션."
NO_DOCS_REASON = "관련 문서가 없어 대표 기사 선정과 요약을 건너뛰었습니다."


def _noop(stage: str, message: str) -> None:
    log.info("[%s] %s", stage, message)


async def run_pipeline(
    period: DateRange,
    extra_topics: list[str] | None = None,
    mode: str = "and",
    excludes: list[str] | None = None,
    top_n: int | None = None,
    collectors: list[str] | None = None,
    progress: ProgressFn | None = None,
    db: Database | None = None,
    use_llm: bool = True,
    use_cache: bool = True,
) -> RunResult:
    """문서 수집부터 종합까지. 리포트 파일 쓰기는 호출자가 결정한다."""
    notify = progress or _noop
    cfg = get_app_config()
    topics = get_topics()
    top_n = top_n or cfg.report.top_n

    owns_db = db is None
    db = db or Database()
    client = build_client(db=db, use_cache=use_cache) if use_llm else None
    llm_skipped_reason: str | None = None
    if client is None:
        llm_skipped_reason = NO_KEY_REASON if use_llm else NO_LLM_FLAG_REASON
        notify("LLM", llm_skipped_reason)

    try:
        queries = build_queries(extra_topics, mode)
        if client is not None and extra_topics:
            notify("질의어", "추가 주제를 LLM으로 확장")
            expanded = await llm_service.expand_queries(client, extra_topics)
            fresh = [q for q in expanded if q not in queries]
            if fresh:
                # 규칙 기반 질의어가 상한을 다 채우면 확장 결과가 통째로
                # 잘려나가 호출이 헛돈다. 확장분 자리를 먼저 떼어 둔다.
                cap = cfg.collect.max_queries
                reserved = min(len(fresh), max(1, cap // 3))
                queries = queries[: cap - reserved] + fresh[:reserved]
                notify(
                    "질의어",
                    f"확장 {len(fresh)}개 중 {reserved}개 반영, 총 {len(queries)}개",
                )
            else:
                notify("질의어", "확장 결과가 기존 질의어와 겹쳐 반영하지 않음")

        context = RunContext(
            date_from=period.start,
            date_to=period.end,
            base_topic=topics.base_topic,
            extra_topics=list(extra_topics or []),
            mode=mode,
            excludes=list(excludes or []),
            queries=queries,
            top_n=top_n,
        )

        async with HttpFetcher() as fetcher:
            notify("수집", f"질의어 {len(queries)}개로 수집 시작")
            adapters = build_collectors(fetcher, collectors)
            results: list[CollectorResult] = list(
                await asyncio.gather(*(a.collect(queries, period) for a in adapters))
            )
            raw_docs: list[Document] = [d for r in results for d in r.documents]
            notify("수집", f"원시 {len(raw_docs)}건")

            notify("중복제거", "URL·제목 기준 병합")
            deduped = dedupe(raw_docs)
            notify("중복제거", f"{len(raw_docs)}건 → {len(deduped)}건")

            in_period = [d for d in deduped if period.contains(d.published_at)]
            date_unknown = [d for d in deduped if d.published_at is None]
            notify(
                "기간필터",
                f"기간 내 {len(in_period)}건, 일자미확인 {len(date_unknown)}건",
            )

            # 본문 예산을 상위 문서에 쓰기 위해 임시 점수로 순서를 잡는다.
            score_documents(in_period, period, excludes)
            notify("본문추출", f"상위 {cfg.collect.body_fetch_limit}건 본문 수집")
            await fetch_bodies(
                in_period, fetcher, db=db, limit=cfg.collect.body_fetch_limit
            )

        notify("분류", "설비·정책·기술·시장 카테고리 부여")
        categorize_documents(in_period)
        categorize_documents(date_unknown)

        semantic: dict[str, float] | None = None
        if client is not None:
            notify("임베딩", "주제-문서 의미 유사도 계산")
            semantic = await semantic_scores(
                in_period, client, db=db, extra_topics=extra_topics
            )
            if semantic is None:
                notify("임베딩", "실패 - 키워드 지표만으로 점수를 냅니다")
        notify("스코어링", "최종 점수 계산")
        score_documents(in_period, period, excludes, semantic_scores=semantic)
        db.save_documents(in_period)

        visible = [
            d for d in in_period if (d.score or 0) >= cfg.scoring.min_score_to_show
        ]

        representative: list[Document] = []
        insights: dict[str, llm_service.DocumentInsight] = {}
        synthesis = None
        if client is not None and visible:
            candidates = visible[: cfg.scoring.list_size]
            notify("대표선정", f"상위 {len(candidates)}건에서 {top_n}건 선정")
            picks = await llm_service.select_representative(client, candidates, top_n)
            if picks:
                by_id = {d.id: d for d in candidates}
                representative = [by_id[i] for i in picks if i in by_id]
            else:
                representative = candidates[:top_n]
                notify("대표선정", "LLM 선정 실패 - 점수 상위로 대체")

            notify("요약", f"대표 기사 {len(representative)}건 요약·시사점 생성")
            insights = await llm_service.summarize_documents(client, representative)
            for doc_id, reason in picks.items():
                if doc_id in insights:
                    insights[doc_id].selection_reason = reason

            notify("종합", "기간 전체 트렌드·시사점 도출")
            synthesis = await llm_service.synthesize(
                client,
                representative,
                insights,
                period.label(),
                dict(Counter(d.category for d in representative)),
            )
        elif client is not None and not visible:
            llm_skipped_reason = NO_DOCS_REASON
            notify("LLM", llm_skipped_reason)

        stats = {
            "raw": len(raw_docs),
            "deduped": len(deduped),
            "in_period": len(in_period),
            "date_unknown": len(date_unknown),
            "with_body": sum(1 for d in in_period if d.has_body),
            "visible": len(visible),
            "by_region": dict(Counter(d.region for d in in_period)),
            "by_doc_type": dict(Counter(d.doc_type for d in in_period)),
            "by_category": dict(Counter(d.category for d in visible)),
            "by_collector": dict(Counter(d.collector for d in in_period)),
            "semantic_applied": semantic is not None,
        }

        return RunResult(
            context=context,
            period=period,
            documents=in_period,
            collector_results=results,
            date_unknown=date_unknown,
            stats=stats,
            representative=representative,
            insights=insights,
            synthesis=synthesis,
            usage=client.usage.to_dict() if client is not None else {},
            llm_skipped_reason=llm_skipped_reason,
        )
    except TokenBudgetExceeded as exc:
        log.error("%s", exc)
        raise
    finally:
        if client is not None:
            await client.aclose()
        if owns_db:
            db.close()


def run_and_write(
    period: DateRange,
    output_dir: Path | None = None,
    progress: ProgressFn | None = None,
    **kwargs: object,
) -> tuple[RunResult, dict[str, Path]]:
    """동기 진입점. CLI와 UI가 이 함수만 부르면 된다."""
    result = asyncio.run(run_pipeline(period, progress=progress, **kwargs))
    target = output_dir or make_output_dir(result.context.started_at)
    paths = write_outputs(result, target)
    with Database() as db:
        db.save_run(str(target), result.context.to_dict())
    return result, paths

"""raw.json을 다시 읽어 수집 없이 리포트만 재생성한다.

대표 기사를 바꿔보거나 프롬프트를 고친 뒤 다시 뽑을 때 쓴다.
수집·본문추출·임베딩을 건너뛰므로 네트워크 비용이 들지 않고,
LLM 응답도 캐시가 있으면 재사용된다.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import get_app_config
from .daterange import DateRange
from .llm import build_client
from .llm import service as llm_service
from .models import CollectorResult, Document, RunContext
from .report import RunResult, write_outputs
from .storage import Database

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, str], None]


class RerunError(RuntimeError):
    """raw.json이 없거나 형식이 맞지 않을 때."""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _document_from_dict(data: dict) -> Document:
    doc = Document(
        title=data.get("title", ""),
        url=data.get("url", ""),
        source_name=data.get("source_name", ""),
        published_at=_parse_dt(data.get("published_at")),
        language=data.get("language", "ko"),
        region=data.get("region", "국내"),
        doc_type=data.get("doc_type", "기사"),
        snippet=data.get("snippet", ""),
        category=data.get("category", "기타"),
        collector=data.get("collector", ""),
        duplicate_count=data.get("duplicate_count", 1),
        id=data.get("id", ""),
    )
    doc.score = data.get("score")
    doc.score_detail = data.get("score_detail", {})
    doc.notes = list(data.get("notes", []))
    return doc


def load_run(output_dir: Path, db: Database | None = None) -> RunResult:
    """raw.json에서 RunResult를 복원한다. 본문은 DB 캐시에서 되살린다."""
    raw_path = Path(output_dir) / "raw.json"
    if not raw_path.exists():
        raise RerunError(f"raw.json이 없습니다: {raw_path}")
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RerunError(f"raw.json을 읽을 수 없습니다: {exc}") from exc

    ctx_data = raw.get("context", {})
    period_data = raw.get("period", {})
    start = _parse_dt(period_data.get("start"))
    end = _parse_dt(period_data.get("end"))
    if start is None or end is None:
        raise RerunError("raw.json에 기간 정보가 없습니다.")
    period = DateRange(start=start, end=end)

    context = RunContext(
        date_from=start,
        date_to=end,
        base_topic=ctx_data.get("base_topic", ""),
        extra_topics=list(ctx_data.get("extra_topics", [])),
        mode=ctx_data.get("mode", "and"),
        excludes=list(ctx_data.get("excludes", [])),
        queries=list(ctx_data.get("queries", [])),
        top_n=ctx_data.get("top_n", get_app_config().report.top_n),
        started_at=_parse_dt(ctx_data.get("started_at")) or start,
    )

    documents = [_document_from_dict(d) for d in raw.get("documents", [])]
    date_unknown = [_document_from_dict(d) for d in raw.get("date_unknown", [])]

    # raw.json은 본문을 담지 않는다(용량). DB 캐시에서 되살린다.
    if db is not None:
        for doc in documents:
            body = db.get_body(doc.url)
            if body:
                doc.body = body

    collectors = [
        CollectorResult(
            collector=c.get("collector", "?"),
            ok=c.get("status") != "실패",
            skipped_reason=c.get("skipped_reason"),
            errors=list(c.get("errors", [])),
        )
        for c in raw.get("collectors", [])
    ]

    return RunResult(
        context=context,
        period=period,
        documents=documents,
        collector_results=collectors,
        date_unknown=date_unknown,
        stats=raw.get("stats", {}),
    )


async def rerun_llm(
    output_dir: Path,
    top_n: int | None = None,
    pick_ids: list[str] | None = None,
    progress: ProgressFn | None = None,
    use_cache: bool = True,
) -> tuple[RunResult, dict[str, Path]]:
    """수집 없이 대표 기사 선정부터 다시 한다.

    pick_ids를 주면 LLM 선정을 건너뛰고 그 문서들을 대표로 쓴다.
    """
    notify = progress or (lambda stage, msg: log.info("[%s] %s", stage, msg))
    cfg = get_app_config()
    output_dir = Path(output_dir)

    with Database() as db:
        notify("불러오기", f"{output_dir.name}의 raw.json 복원")
        result = load_run(output_dir, db=db)
        notify(
            "불러오기",
            f"문서 {len(result.documents)}건 복원, 본문 보유 "
            f"{sum(1 for d in result.documents if d.has_body)}건",
        )

        client = build_client(db=db, use_cache=use_cache)
        if client is None:
            result.llm_skipped_reason = (
                "LLM 단계를 건너뛰었습니다 - OPENAI_API_KEY 미설정."
            )
            paths = write_outputs(result, output_dir)
            return result, paths

        top_n = top_n or result.context.top_n or cfg.report.top_n
        visible = result.visible
        candidates = visible[: cfg.scoring.list_size]

        try:
            if pick_ids:
                by_id = {d.id: d for d in result.documents}
                missing = [i for i in pick_ids if i not in by_id]
                if missing:
                    raise RerunError(
                        f"지정한 문서 id를 찾을 수 없습니다: {', '.join(missing)}"
                    )
                result.representative = [by_id[i] for i in pick_ids]
                notify("대표선정", f"사용자 지정 {len(result.representative)}건")
                picks: dict[str, str] = {}
            elif candidates:
                notify("대표선정", f"상위 {len(candidates)}건에서 {top_n}건 선정")
                picks = await llm_service.select_representative(
                    client, candidates, top_n
                )
                if picks:
                    by_id = {d.id: d for d in candidates}
                    result.representative = [by_id[i] for i in picks if i in by_id]
                else:
                    result.representative = candidates[:top_n]
                    notify("대표선정", "LLM 선정 실패 - 점수 상위로 대체")
            else:
                picks = {}
                result.llm_skipped_reason = "관련 문서가 없어 요약을 건너뛰었습니다."

            if result.representative:
                notify("요약", f"대표 기사 {len(result.representative)}건 요약")
                result.insights = await llm_service.summarize_documents(
                    client, result.representative
                )
                for doc_id, reason in picks.items():
                    if doc_id in result.insights:
                        result.insights[doc_id].selection_reason = reason

                notify("종합", "기간 전체 트렌드·시사점 도출")
                result.synthesis = await llm_service.synthesize(
                    client,
                    result.representative,
                    result.insights,
                    result.period.label(),
                    dict(Counter(d.category for d in result.representative)),
                )
            result.usage = client.usage.to_dict()
            result.stats["by_category"] = dict(Counter(d.category for d in visible))
        finally:
            await client.aclose()

        paths = write_outputs(result, output_dir)
        return result, paths

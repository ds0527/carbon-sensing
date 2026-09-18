"""CLI 진입점. 인자로도, 대화형으로도 기간·주제를 받는다."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated, Optional

import click
import typer

from .collectors import ALL_COLLECTOR_NAMES
from .config import ensure_utf8_stdout, get_app_config, get_settings, get_topics
from .daterange import PRESETS, DateRange, DateRangeError, build_range, preset_range
from .llm import TokenBudgetExceeded
from .pipeline import run_and_write
from .report import OUTPUT_ROOT

app = typer.Typer(
    add_completion=False,
    help="철강산업 탄소중립 동향 센싱 (1단계: 수집·스코어링·목록)",
)


def _setup_logging(verbose: bool) -> None:
    ensure_utf8_stdout()
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _ask(message: str, default: str | None = None) -> str:
    """대화형 입력. 입력이 닫힌 환경에서는 기본값으로 조용히 넘어간다."""
    if not _interactive():
        return default or ""
    try:
        return typer.prompt(message, default=default, show_default=False)
    except Exception:
        # isatty()가 True를 반환해도 실제로는 입력을 받을 수 없는 환경이 있다
        # (예: 일부 CI·샌드박스 셸). 어떤 이유로 실패하든 프롬프트 때문에
        # 전체 실행이 멈추면 안 되므로 기본값으로 조용히 넘어간다.
        typer.echo("")  # 프롬프트 줄을 닫아준다
        return default or ""


def _resolve_period(
    date_from: str | None, date_to: str | None, preset: str | None
) -> DateRange:
    if preset:
        return preset_range(preset)
    if date_from and date_to:
        return build_range(date_from, date_to)
    if not _interactive():
        raise DateRangeError(
            "기간이 없습니다. --from/--to 또는 --preset을 지정하세요 "
            "(대화형 입력은 터미널에서만 됩니다)."
        )
    typer.echo("기간을 입력하세요. (프리셋: " + ", ".join(PRESETS) + ")")
    raw_preset = _ask("프리셋 (직접 입력하려면 빈칸)", default="")
    if raw_preset.strip():
        return preset_range(raw_preset.strip())
    start = _ask("시작일 (YYYY-MM-DD)")
    end = _ask("종료일 (YYYY-MM-DD)")
    if not start or not end:
        raise DateRangeError("시작일과 종료일이 모두 필요합니다.")
    return build_range(start, end)


def _resolve_topics(topics: list[str] | None) -> list[str]:
    if topics:
        return [t.strip() for t in topics if t.strip()]
    raw = _ask("추가 주제 (쉼표로 구분, 없으면 빈칸)", default="")
    return [t.strip() for t in raw.split(",") if t.strip()]


def _progress(stage: str, message: str) -> None:
    typer.echo(f"  [{stage}] {message}")


@app.command()
def run(
    date_from: Annotated[
        Optional[str], typer.Option("--from", help="시작일 YYYY-MM-DD")
    ] = None,
    date_to: Annotated[
        Optional[str], typer.Option("--to", help="종료일 YYYY-MM-DD")
    ] = None,
    preset: Annotated[
        Optional[str],
        typer.Option("--preset", help=f"기간 프리셋: {', '.join(PRESETS)}"),
    ] = None,
    topic: Annotated[
        Optional[list[str]],
        typer.Option("--topic", help="추가 주제 (여러 번 지정 가능)"),
    ] = None,
    mode: Annotated[
        str, typer.Option("--mode", help="추가 주제 결합 방식: and | or")
    ] = "and",
    exclude: Annotated[
        Optional[list[str]], typer.Option("--exclude", help="제외어 (여러 번 지정 가능)")
    ] = None,
    top_k: Annotated[
        Optional[int], typer.Option("--top-k", help="대표 기사 건수 (기본 5)")
    ] = None,
    collector: Annotated[
        Optional[list[str]],
        typer.Option("--collector", help=f"사용할 수집기: {', '.join(ALL_COLLECTOR_NAMES)}"),
    ] = None,
    show_all: Annotated[
        bool, typer.Option("--show-all", help="40점 미만 문서까지 콘솔에 표시")
    ] = False,
    no_llm: Annotated[
        bool,
        typer.Option("--no-llm", help="LLM·임베딩 단계를 건너뛰고 목록만 만든다"),
    ] = False,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="LLM·임베딩 캐시를 쓰지 않는다")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="진행 로그 출력")] = False,
) -> None:
    """기간 내 문서를 수집해 관련도 순 목록 리포트를 만든다."""
    _setup_logging(verbose)
    settings = get_settings()
    cfg = get_app_config()

    try:
        period = _resolve_period(date_from, date_to, preset)
    except DateRangeError as exc:
        typer.secho(f"기간 오류: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    if period.days > cfg.collect.long_period_warning_days:
        typer.secho(
            f"경고: 기간이 {period.days}일입니다. 수집량과 API 사용량이 크게 늘어납니다.",
            fg=typer.colors.YELLOW,
        )

    extra_topics = _resolve_topics(list(topic) if topic else None)
    mode_normalized = mode.strip().lower()
    if mode_normalized not in ("and", "or"):
        typer.secho("--mode는 and 또는 or만 됩니다.", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    missing = settings.missing_keys()
    for name, keys in missing.items():
        typer.secho(
            f"알림: {name} 수집기를 건너뜁니다 (.env에 {', '.join(keys)} 없음)",
            fg=typer.colors.YELLOW,
        )

    typer.echo("")
    typer.echo(f"기간   : {period.label()} ({period.days}일)")
    typer.echo(f"기본   : {get_topics().base_topic}")
    typer.echo(f"추가   : {', '.join(extra_topics) if extra_topics else '없음'} [{mode_normalized.upper()}]")
    typer.echo("")

    try:
        result, paths = run_and_write(
            period,
            extra_topics=extra_topics,
            mode=mode_normalized,
            excludes=list(exclude) if exclude else None,
            top_n=top_k,
            collectors=list(collector) if collector else None,
            progress=_progress,
            use_llm=not no_llm,
            use_cache=not no_cache,
        )
    except TokenBudgetExceeded as exc:
        typer.secho(f"중단: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=3)

    typer.echo("")
    shown = result.documents if show_all else result.visible
    limit = cfg.scoring.list_size
    typer.echo(f"관련도 상위 {min(len(shown), limit)}건")
    typer.echo("-" * 72)
    for i, doc in enumerate(shown[:limit], start=1):
        date_text = (
            doc.published_at.strftime("%m-%d") if doc.published_at else "일자미확인"
        )
        typer.echo(f"{i:2d}. [{doc.score:5.1f}] [{doc.category}] {doc.title[:55]}")
        typer.echo(f"     {doc.source_name} · {date_text} · {doc.url}")
    typer.echo("-" * 72)

    if result.representative:
        typer.echo("")
        typer.secho("대표 기사", bold=True)
        for category, members in result.by_category().items():
            typer.echo(f"  [{category}]")
            for doc in members:
                typer.echo(f"    - {doc.title[:60]}")
                typer.echo(f"      {doc.source_name} · {doc.url}")
    if result.synthesis is not None and result.synthesis.headline:
        typer.echo("")
        typer.secho("종합", bold=True)
        typer.echo(f"  {result.synthesis.headline}")
        for claim in result.synthesis.trends:
            typer.echo(f"  - {claim.text}")
    if result.llm_skipped_reason:
        typer.echo("")
        typer.secho(result.llm_skipped_reason, fg=typer.colors.YELLOW)

    typer.echo("")
    stats = result.stats
    typer.echo(
        f"원시 {stats['raw']}건 → 중복제거 {stats['deduped']}건 "
        f"→ 기간 내 {stats['in_period']}건 → 관련 {len(result.visible)}건 "
        f"→ 대표 {len(result.representative)}건"
    )
    if stats.get("by_category"):
        cat_text = ", ".join(f"{k} {v}건" for k, v in stats["by_category"].items())
        typer.echo(f"카테고리: {cat_text}")
    if result.usage:
        usage = result.usage
        typer.echo(
            f"LLM 사용량: {usage.get('total_tokens', 0):,} 토큰 · "
            f"호출 {usage.get('calls', 0)}회 · 캐시 {usage.get('cached_calls', 0)}회 · "
            f"추정 USD {usage.get('estimated_cost_usd', 0)}"
        )
    layout = stats.get("summary_layout") or {}
    if layout and not layout.get("fits_one_page", True):
        typer.secho(
            f"주의: summary.md가 1장을 넘었습니다({layout.get('chars')}자).",
            fg=typer.colors.YELLOW,
        )
    typer.echo("")
    for label, key in (
        ("1장 요약", "summary"),
        ("상세 리포트", "report"),
        ("엑셀 목록", "xlsx"),
        ("원본 JSON", "raw"),
    ):
        if key in paths:
            typer.echo(f"{label}: {paths[key]}")


@app.command()
def rerun(
    output_dir: Annotated[
        str, typer.Argument(help="다시 생성할 outputs 폴더 경로")
    ],
    top_k: Annotated[
        Optional[int], typer.Option("--top-k", help="대표 기사 건수")
    ] = None,
    pick: Annotated[
        Optional[list[str]],
        typer.Option("--pick", help="대표로 쓸 문서 id (여러 번 지정 가능)"),
    ] = None,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="LLM 캐시를 쓰지 않는다")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="진행 로그 출력")] = False,
) -> None:
    """수집을 건너뛰고 대표 기사 선정부터 다시 해 리포트를 재생성한다."""
    import asyncio

    from .rerun import RerunError, rerun_llm

    _setup_logging(verbose)
    target = Path(output_dir)
    if not target.exists():
        typer.secho(f"폴더가 없습니다: {target}", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    try:
        result, paths = asyncio.run(
            rerun_llm(
                target,
                top_n=top_k,
                pick_ids=list(pick) if pick else None,
                progress=_progress,
                use_cache=not no_cache,
            )
        )
    except RerunError as exc:
        typer.secho(f"재생성 실패: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    except TokenBudgetExceeded as exc:
        typer.secho(f"중단: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=3)

    typer.echo("")
    if result.representative:
        typer.secho("대표 기사", bold=True)
        for category, members in result.by_category().items():
            typer.echo(f"  [{category}]")
            for doc in members:
                typer.echo(f"    - [{doc.id}] {doc.title[:58]}")
    if result.synthesis is not None and result.synthesis.headline:
        typer.echo("")
        typer.echo(f"종합: {result.synthesis.headline}")
    if result.usage:
        usage = result.usage
        typer.echo("")
        typer.echo(
            f"LLM 사용량: {usage.get('total_tokens', 0):,} 토큰 · "
            f"캐시 {usage.get('cached_calls', 0)}회 · "
            f"추정 USD {usage.get('estimated_cost_usd', 0)}"
        )
    typer.echo("")
    for label, key in (
        ("1장 요약", "summary"),
        ("상세 리포트", "report"),
        ("엑셀 목록", "xlsx"),
        ("원본 JSON", "raw"),
    ):
        if key in paths:
            typer.echo(f"{label}: {paths[key]}")


@app.command()
def check() -> None:
    """설정 점검. .env 변수와 yaml 로드 상태만 확인하고 아무것도 수집하지 않는다."""
    ensure_utf8_stdout()
    settings = get_settings()
    topics = get_topics()
    cfg = get_app_config()

    typer.echo("[.env]")
    for label, ready in (
        ("NAVER_CLIENT_ID / SECRET", settings.naver_ready),
        ("TAVILY_API_KEY", settings.tavily_ready),
        ("OPENAI_API_KEY (2단계)", bool(settings.openai_api_key)),
    ):
        mark = "설정됨" if ready else "없음"
        typer.echo(f"  - {label}: {mark}")
    typer.echo(f"  - OPENAI_MODEL: {settings.openai_model}")
    typer.echo("")
    typer.echo("[yaml]")
    typer.echo(f"  - 기본 주제: {topics.base_topic}")
    typer.echo(f"  - 기본 질의어: {len(topics.base_queries_ko) + len(topics.base_queries_en)}개")
    typer.echo(
        f"  - 철강 키워드: {len(topics.steel_keywords)}개 · "
        f"탄소 키워드: {len(topics.carbon_keywords)}개 · "
        f"보너스 키워드: {len(topics.bonus_keywords)}개"
    )
    typer.echo(f"  - 가중치: {cfg.scoring.weights.as_dict()}")
    typer.echo(f"  - 표시 임계값: {cfg.scoring.min_score_to_show}점")
    typer.echo("")
    typer.echo(f"출력 폴더: {OUTPUT_ROOT}")


@app.command()
def presets() -> None:
    """기간 프리셋과 그 실제 범위를 보여준다."""
    ensure_utf8_stdout()
    for key, label in PRESETS.items():
        period = preset_range(key)
        typer.echo(f"  {key:<12} {label:<10} {period.label()} ({period.days}일)")


def main() -> None:
    app()


if __name__ == "__main__":
    main()

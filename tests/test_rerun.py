"""rerun: raw.json 복원이 문서·기간·점수를 그대로 되살리는지."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from carbon_sensing.daterange import build_range
from carbon_sensing.models import CollectorResult, RunContext
from carbon_sensing.report import RunResult, write_outputs
from carbon_sensing.rerun import RerunError, load_run
from tests.conftest import make_doc


def _write_run(tmp_path: Path) -> Path:
    period = build_range("2026-09-01", "2026-09-15")
    docs = []
    for i in range(3):
        doc = make_doc(title=f"철강 탄소중립 기사 {i}", url=f"https://yna.co.kr/{i}")
        doc.category = ["정책", "설비", "기술"][i]
        doc.score = 60.0 - i
        doc.score_detail = {"off_topic": None}
        docs.append(doc)
    ctx = RunContext(
        date_from=period.start,
        date_to=period.end,
        base_topic="철강산업의 탄소중립",
        extra_topics=["CBAM"],
        mode="and",
        excludes=["주가"],
        queries=["철강 탄소중립 CBAM"],
        top_n=5,
    )
    result = RunResult(
        context=ctx,
        period=period,
        documents=docs,
        collector_results=[CollectorResult(collector="rss", documents=docs)],
        stats={"raw": 10, "in_period": 3},
    )
    write_outputs(result, tmp_path)
    return tmp_path


def test_load_run_restores_documents(tmp_path):
    loaded = load_run(_write_run(tmp_path))
    assert len(loaded.documents) == 3
    assert [d.category for d in loaded.documents] == ["정책", "설비", "기술"]
    assert loaded.documents[0].score == 60.0


def test_load_run_restores_period_and_context(tmp_path):
    loaded = load_run(_write_run(tmp_path))
    assert loaded.period.label() == "2026-09-01 ~ 2026-09-15"
    assert loaded.context.extra_topics == ["CBAM"]
    assert loaded.context.excludes == ["주가"]
    assert loaded.context.mode == "and"


def test_load_run_restores_document_ids(tmp_path):
    """id가 유지되지 않으면 --pick으로 대표를 지정할 수 없다."""
    path = _write_run(tmp_path)
    raw = json.loads((path / "raw.json").read_text(encoding="utf-8"))
    original_ids = [d["id"] for d in raw["documents"]]
    loaded = load_run(path)
    assert [d.id for d in loaded.documents] == original_ids


def test_missing_raw_json_raises(tmp_path):
    with pytest.raises(RerunError, match="raw.json"):
        load_run(tmp_path)


def test_malformed_raw_json_raises(tmp_path):
    (tmp_path / "raw.json").write_text("{깨진 json", encoding="utf-8")
    with pytest.raises(RerunError):
        load_run(tmp_path)


def test_raw_json_without_period_raises(tmp_path):
    (tmp_path / "raw.json").write_text(
        json.dumps({"context": {}, "documents": []}), encoding="utf-8"
    )
    with pytest.raises(RerunError, match="기간"):
        load_run(tmp_path)


def test_write_outputs_creates_all_four_artifacts(tmp_path):
    path = _write_run(tmp_path)
    for name in ("summary.md", "report.md", "raw.json", "articles.xlsx"):
        assert (path / name).exists(), f"{name}이 생성되지 않았다"

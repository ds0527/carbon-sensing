"""UI 헬퍼: 수집기 가용성, 비용 추정, 이력, 프로필. Streamlit 없이 검증한다."""

from __future__ import annotations

import json

import pytest

from carbon_sensing.ui import runner
from carbon_sensing.ui.theme import CATEGORY_STYLE, chip_html


def test_collector_availability_lists_all():
    rows = runner.collector_availability()
    names = {r["name"] for r in rows}
    assert names == {"naver_news", "web_search", "rss", "openalex", "gdelt"}


def test_keyless_collectors_are_available():
    rows = {r["name"]: r for r in runner.collector_availability()}
    for name in ("rss", "openalex", "gdelt"):
        assert rows[name]["available"], f"{name}은 키가 필요 없다"


def test_unavailable_collector_names_the_env_var_not_the_value():
    rows = {r["name"]: r for r in runner.collector_availability()}
    reason = rows["naver_news"]["reason"]
    if reason:
        assert "NAVER_CLIENT_ID" in reason
        assert "sk-" not in reason, "키 값이 노출되면 안 된다"


def test_cost_estimate_grows_with_top_k():
    small = runner.estimate_cost(12, 3)
    large = runner.estimate_cost(12, 10)
    assert large["total_tokens"] > small["total_tokens"]
    assert large["cost_usd"] >= small["cost_usd"]


def test_cost_estimate_flags_over_budget():
    est = runner.estimate_cost(12, 10, doc_count=100000)
    assert est["over_budget"] is True


def test_progress_tracker_fraction_advances():
    tracker = runner.ProgressTracker()
    assert tracker.fraction == 0.0
    tracker("수집", "시작")
    first = tracker.fraction
    tracker("종합", "끝")
    assert tracker.fraction > first
    assert tracker.fraction <= 1.0


def test_progress_tracker_labels_stages_in_korean():
    tracker = runner.ProgressTracker()
    tracker("대표선정", "5건")
    assert tracker.label == "대표 기사 선정"
    assert "대표 기사 선정" in tracker.rendered()


def test_progress_tracker_handles_unknown_stage():
    tracker = runner.ProgressTracker()
    tracker("알수없는단계", "메시지")
    assert tracker.fraction == 1.0
    assert tracker.label == "알수없는단계"


def test_all_stages_have_labels():
    for stage in runner.STAGES:
        assert stage in runner.STAGE_LABEL, f"{stage} 라벨 없음"


def test_chip_html_differs_per_category():
    marks = {c: chip_html(c) for c in CATEGORY_STYLE}
    assert len(set(marks.values())) == len(marks), "카테고리 칩이 구분되지 않는다"
    for category, html in marks.items():
        assert category in html


def test_chip_html_handles_unknown_category():
    assert "cs-chip" in chip_html("새로운카테고리")


def test_list_history_reads_outputs(tmp_path, monkeypatch):
    folder = tmp_path / "2026-09-18_1200"
    folder.mkdir()
    (folder / "raw.json").write_text(
        json.dumps(
            {
                "context": {"extra_topics": ["CBAM"]},
                "period": {"label": "2026-09-01 ~ 2026-09-15"},
                "stats": {"visible": 7},
                "representative": ["a", "b"],
            }
        ),
        encoding="utf-8",
    )
    (folder / "summary.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(runner, "OUTPUT_ROOT", tmp_path)
    entries = runner.list_history()
    assert len(entries) == 1
    assert entries[0].visible == 7
    assert entries[0].representative == 2
    assert entries[0].has_summary is True


def test_list_history_skips_folders_without_raw_json(tmp_path, monkeypatch):
    (tmp_path / "empty_folder").mkdir()
    monkeypatch.setattr(runner, "OUTPUT_ROOT", tmp_path)
    assert runner.list_history() == []


def test_list_history_tolerates_broken_json(tmp_path, monkeypatch):
    folder = tmp_path / "2026-09-18_1300"
    folder.mkdir()
    (folder / "raw.json").write_text("{깨진", encoding="utf-8")
    monkeypatch.setattr(runner, "OUTPUT_ROOT", tmp_path)
    assert runner.list_history() == []


def test_save_profile_rejects_empty_name():
    with pytest.raises(ValueError):
        runner.save_profile("", ["CBAM"], "and", [])


def test_profile_roundtrip():
    name = "__테스트프로필__"
    try:
        runner.save_profile(name, ["CBAM", "배출권"], "or", ["주가"])
        profiles = runner.list_profiles()
        assert name in profiles
        assert profiles[name]["extra_topics"] == ["CBAM", "배출권"]
        assert profiles[name]["mode"] == "or"
    finally:
        runner.delete_profile(name)
    assert name not in runner.list_profiles()

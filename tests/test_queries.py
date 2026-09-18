"""질의어 생성: AND/OR, 상한, 중복 제거."""

from __future__ import annotations

import pytest

from carbon_sensing.config import get_app_config
from carbon_sensing.queries import build_queries, split_language


def test_no_extra_topic_uses_base_only():
    queries = build_queries([])
    assert queries
    on_topic = ("철강", "제철", "제강", "steel", "iron", "dri")
    for q in queries:
        assert any(word in q.lower() for word in on_topic), f"주제와 무관한 질의어: {q}"


def test_and_mode_combines_base_and_extra():
    queries = build_queries(["CBAM"], "and")
    assert all("CBAM" in q for q in queries)


def test_or_mode_keeps_base_queries():
    queries = build_queries(["CBAM"], "or")
    assert any("CBAM" not in q for q in queries)


def test_query_limit_respected():
    limit = get_app_config().collect.max_queries
    queries = build_queries(["CBAM", "배출권", "전기로", "수소"], "and")
    assert len(queries) <= limit


def test_no_duplicate_queries():
    queries = build_queries(["CBAM", "cbam", " CBAM "], "and")
    assert len(queries) == len({q.lower() for q in queries})


def test_bad_mode_raises():
    with pytest.raises(ValueError):
        build_queries(["CBAM"], "xor")


def test_split_language():
    ko, en = split_language(["철강 탄소중립", "green steel"])
    assert ko == ["철강 탄소중립"]
    assert en == ["green steel"]


def test_all_extras_appear_in_and_mode():
    extras = ["CBAM", "배출권"]
    queries = build_queries(extras, "and")
    for extra in extras:
        assert any(extra in q for q in queries), f"{extra}가 질의어에 없다"


def test_no_duplicate_topic_in_query():
    """기본 질의어에 이미 주제가 있으면 또 붙이지 않는다."""
    queries = build_queries(["CBAM"], "and")
    doubled = [q for q in queries if q.lower().count("cbam") > 1]
    assert not doubled, f"주제가 중복 부착됨: {doubled}"


def test_no_duplicate_topic_case_insensitive():
    queries = build_queries(["green steel"], "and")
    doubled = [q for q in queries if q.lower().count("green steel") > 1]
    assert not doubled


def test_extra_topic_still_applied_when_not_present():
    queries = build_queries(["배출권"], "and")
    assert all("배출권" in q for q in queries)

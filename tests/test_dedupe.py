"""중복 제거: URL 정규화, 제목 유사도, 병합 시 어느 쪽이 남는지."""

from __future__ import annotations

from carbon_sensing.processing.dedupe import (
    dedupe,
    normalize_title,
    normalize_url,
    title_similarity,
)
from tests.conftest import make_doc


def test_tracking_params_stripped():
    a = normalize_url("https://www.yna.co.kr/view/A1?utm_source=naver&fbclid=xyz")
    b = normalize_url("http://yna.co.kr/view/A1/")
    assert a == b


def test_meaningful_query_kept():
    assert normalize_url("https://a.kr/news?id=12") != normalize_url("https://a.kr/news?id=13")


def test_query_order_does_not_matter():
    assert normalize_url("https://a.kr/n?b=2&a=1") == normalize_url("https://a.kr/n?a=1&b=2")


def test_fragment_dropped():
    assert normalize_url("https://a.kr/n#section2") == normalize_url("https://a.kr/n")


def test_title_noise_stripped():
    assert normalize_title("[단독] 포스코, 수소환원제철 착공 (종합2보)") == normalize_title(
        "포스코, 수소환원제철 착공"
    )


def test_similar_titles_merge():
    docs = [
        make_doc(title="포스코, 수소환원제철 실증설비 착공", url="https://yna.co.kr/a"),
        make_doc(
            title="[단독] 포스코, 수소환원제철 실증설비 착공",
            url="https://news1.kr/b",
            source_name="news1.kr",
        ),
    ]
    merged = dedupe(docs)
    assert len(merged) == 1
    assert merged[0].duplicate_count == 2


def test_higher_trust_source_survives():
    docs = [
        make_doc(title="철강 탄소중립 로드맵 발표", url="https://naver.com/x", source_name="naver.com", body=None),
        make_doc(title="철강 탄소중립 로드맵 발표", url="https://motie.go.kr/y", source_name="motie.go.kr"),
    ]
    merged = dedupe(docs)
    assert len(merged) == 1
    assert "motie.go.kr" in merged[0].url


def test_different_articles_are_not_merged():
    docs = [
        make_doc(title="포스코, 수소환원제철 실증설비 착공", url="https://a.kr/1"),
        make_doc(
            title="현대제철, 전기로 증설 투자 확정",
            url="https://b.kr/2",
            source_name="b.kr",
        ),
    ]
    assert len(dedupe(docs)) == 2


def test_same_title_far_apart_in_time_is_not_merged():
    docs = [
        make_doc(title="철강 탄소중립 간담회 개최", url="https://a.kr/1", published="2026-09-01T09:00:00+09:00"),
        make_doc(
            title="철강 탄소중립 간담회 개최",
            url="https://b.kr/2",
            source_name="b.kr",
            published="2026-09-12T09:00:00+09:00",
        ),
    ]
    assert len(dedupe(docs)) == 2


def test_merge_fills_missing_body_and_date():
    docs = [
        make_doc(title="철강 배출권 개편", url="https://naver.com/x", source_name="naver.com", body=None, published=None),
        make_doc(title="철강 배출권 개편", url="https://yna.co.kr/y", body="본문 있음"),
    ]
    merged = dedupe(docs)
    assert len(merged) == 1
    assert merged[0].has_body
    assert merged[0].published_at is not None


def test_duplicate_count_counts_outlets_not_urls():
    docs = [
        make_doc(title="철강 CBAM 대응", url="https://yna.co.kr/1"),
        make_doc(title="철강 CBAM 대응", url="https://yna.co.kr/1?utm_source=x"),
        make_doc(title="철강 CBAM 대응", url="https://mk.co.kr/2", source_name="mk.co.kr"),
    ]
    merged = dedupe(docs)
    assert len(merged) == 1
    assert merged[0].duplicate_count == 2


def test_empty_input():
    assert dedupe([]) == []


def test_similarity_bounds():
    assert title_similarity("", "무언가") == 0.0
    assert title_similarity("같은 제목", "같은 제목") == 1.0

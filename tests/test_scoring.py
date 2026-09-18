"""스코어링: 가중치 재정규화, 제외어, 필수 키워드, 패널티, 정렬."""

from __future__ import annotations

import pytest

from carbon_sensing.config import get_app_config
from carbon_sensing.scoring.keyword import AVAILABLE_METRICS, score_documents, topic_gate
from carbon_sensing.config import get_topics
from tests.conftest import make_doc


def test_score_in_range(period):
    docs = [make_doc()]
    score_documents(docs, period)
    assert 0 <= docs[0].score <= 100


def test_weights_renormalized_to_one(period):
    docs = [make_doc()]
    score_documents(docs, period)
    used = docs[0].score_detail["weights_used"]
    assert set(used) == set(AVAILABLE_METRICS)
    assert abs(sum(used.values()) - 1.0) < 0.01


def test_score_detail_contributions_sum_to_score(period):
    docs = [make_doc()]
    score_documents(docs, period)
    doc = docs[0]
    total = sum(doc.score_detail["contributions"].values())
    # 본문이 있으므로 패널티가 없고 기여도 합이 점수와 같아야 한다.
    assert abs(total - doc.score) < 0.5


def test_exclude_word_in_title_zeroes_score(period):
    docs = [make_doc(title="포스코 주가 전망, 목표주가 상향")]
    score_documents(docs, period, excludes=["주가"])
    assert docs[0].score == 0.0
    assert docs[0].score_detail["off_topic"]


def test_default_excludes_applied_without_user_input(period):
    docs = [make_doc(title="철강 탄소중립 추천주 리스트")]
    score_documents(docs, period)
    assert docs[0].score == 0.0


def test_no_steel_keyword_is_off_topic(period):
    docs = [make_doc(title="반도체 공장 재생에너지 전환", body="반도체 탄소중립 투자", snippet="")]
    score_documents(docs, period)
    assert docs[0].score == 0.0
    assert docs[0].score_detail["off_topic"] == "철강 키워드 없음"


def test_no_carbon_keyword_is_off_topic(period):
    docs = [make_doc(title="포스코 후판 가격 인상", body="철강사 후판 공급 계약", snippet="")]
    score_documents(docs, period)
    assert docs[0].score == 0.0
    assert docs[0].score_detail["off_topic"] == "탄소 키워드 없음"


def test_substring_false_positive_is_rejected(period):
    """'참고로'의 '고로'가 제철 고로로 잡히면 안 된다."""
    docs = [
        make_doc(
            title="경북도 금고 농협이 계속 맡는다",
            body="참고로 이번 선정은 탄소중립 기금과 무관하다",
            snippet="",
        )
    ]
    score_documents(docs, period)
    assert docs[0].score == 0.0
    assert docs[0].score_detail["off_topic"] == "철강 키워드 없음"


def test_seasonal_homonym_is_rejected(period):
    """'제철 과일'의 '제철'이 제철소로 잡히면 안 된다."""
    docs = [
        make_doc(
            title="식품업계, 가을 제철 과일로 신메뉴 경쟁",
            body="제철 농산물을 활용한 저탄소 메뉴를 내놨다",
            snippet="",
        )
    ]
    score_documents(docs, period)
    assert docs[0].score == 0.0


def test_electric_furnace_particle_is_rejected(period):
    """'전기로 인한'이 제강 전기로로 잡히면 안 된다."""
    docs = [
        make_doc(
            title="정전 피해 확산",
            body="정전으로 전기로 인한 손실이 커졌다. 탄소배출 저감 계획도 지연됐다.",
            snippet="",
        )
    ]
    score_documents(docs, period)
    assert docs[0].score == 0.0


def test_on_topic_document_passes_threshold(period):
    docs = [
        make_doc(
            title="포스코, 수소환원제철 실증설비 착공",
            body="포스코가 탄소중립 달성을 위해 수소환원제철 HyREX 실증설비를 착공했다.",
        )
    ]
    score_documents(docs, period)
    assert docs[0].score >= get_app_config().scoring.min_score_to_show


def test_no_body_penalty_lowers_score(period):
    with_body = make_doc(url="https://yna.co.kr/1")
    without_body = make_doc(url="https://yna.co.kr/2", body=None)
    score_documents([with_body], period)
    score_documents([without_body], period)
    assert without_body.score < with_body.score
    assert without_body.score_detail["no_body_penalty"] is True


def test_penalty_applied_once(period):
    doc = make_doc(body=None)
    score_documents([doc], period)
    first = doc.score
    score_documents([doc], period)
    assert doc.score == first


def test_trusted_source_scores_higher(period):
    gov = make_doc(url="https://motie.go.kr/a", source_name="motie.go.kr")
    portal = make_doc(url="https://blog.example.net/a", source_name="example.net")
    score_documents([gov, portal], period)
    assert gov.score > portal.score


def test_subdomain_inherits_tier(period):
    doc = make_doc(url="https://news.yna.co.kr/view/1")
    score_documents([doc], period)
    assert doc.score_detail["source_tier"] == "주요_전문"


def test_recent_document_scores_higher(period):
    older = make_doc(url="https://yna.co.kr/1", published="2026-09-02T09:00:00+09:00")
    newer = make_doc(url="https://yna.co.kr/2", published="2026-09-14T09:00:00+09:00")
    score_documents([older, newer], period)
    assert newer.score > older.score


def test_date_unknown_gets_middle_recency(period):
    doc = make_doc(published=None)
    score_documents([doc], period)
    assert doc.score_detail["components"]["recency"] == 0.5


def test_spread_raises_score(period):
    single = make_doc(url="https://yna.co.kr/1")
    many = make_doc(url="https://yna.co.kr/2")
    many.duplicate_count = 8
    score_documents([single, many], period)
    assert many.score > single.score


def test_sorted_by_score_desc(period):
    docs = [
        make_doc(title="반도체 뉴스", url="https://a.net/1", body="무관", snippet=""),
        make_doc(url="https://motie.go.kr/2", source_name="motie.go.kr"),
    ]
    score_documents(docs, period)
    assert docs[0].score >= docs[1].score


def test_empty_list_does_not_crash(period):
    assert score_documents([], period) == []


def test_all_docs_offtopic_gives_no_visible(period):
    docs = [make_doc(title="부고 알림", body="무관한 내용", snippet="")]
    score_documents(docs, period)
    threshold = get_app_config().scoring.min_score_to_show
    assert all((d.score or 0) < threshold for d in docs)


def test_keyword_lists_configured():
    topics = get_topics()
    assert topics.steel_keywords
    assert topics.carbon_keywords
    assert "제철" not in topics.steel_keywords, "동음이의어 '제철'은 단독으로 쓰지 않는다"


def test_topic_gate_returns_evidence(period):
    doc = make_doc(
        title="현대제철 전기로 증설", body="탄소배출 감축을 위한 전기로 증설 투자"
    )
    off_topic, evidence = topic_gate(
        doc, get_topics().steel_keywords, get_topics().carbon_keywords, []
    )
    assert off_topic is None
    assert evidence["steel_matched"]
    assert evidence["carbon_matched"]


# --- 2단계: 의미 유사도 결합 ---


def test_semantic_uses_full_prd_weights(period):
    doc = make_doc()
    score_documents([doc], period, semantic_scores={doc.id: 0.8})
    used = doc.score_detail["weights_used"]
    assert set(used) == {"semantic", "keyword", "source_trust", "recency", "spread"}
    assert used["semantic"] == pytest.approx(0.40)
    assert used["keyword"] == pytest.approx(0.20)
    assert abs(sum(used.values()) - 1.0) < 0.01


def test_without_semantic_weights_renormalize(period):
    doc = make_doc()
    score_documents([doc], period, semantic_scores=None)
    used = doc.score_detail["weights_used"]
    assert "semantic" not in used
    assert abs(sum(used.values()) - 1.0) < 0.01
    assert doc.score_detail["semantic"] == "임베딩 미적용"


def test_higher_semantic_raises_score(period):
    low = make_doc(url="https://yna.co.kr/1")
    high = make_doc(url="https://yna.co.kr/2")
    score_documents([low], period, semantic_scores={low.id: 0.1})
    score_documents([high], period, semantic_scores={high.id: 0.9})
    assert high.score > low.score


def test_missing_semantic_for_one_doc_renormalizes_only_that_doc(period):
    """임베딩 실패가 그 문서의 점수를 깎지 않아야 한다."""
    covered = make_doc(url="https://yna.co.kr/1")
    missing = make_doc(url="https://yna.co.kr/2")
    score_documents(
        [covered, missing], period, semantic_scores={covered.id: 0.5}
    )
    assert "semantic" not in missing.score_detail["weights_used"]
    assert "semantic" in covered.score_detail["weights_used"]
    assert missing.score_detail["semantic"] == "임베딩 미적용"
    assert abs(sum(missing.score_detail["weights_used"].values()) - 1.0) < 0.01
    assert missing.score > 0


def test_no_body_penalty_not_double_applied_with_semantic(period):
    """semantic이 있으면 전체 점수에 본문 패널티를 다시 걸지 않는다."""
    doc = make_doc(body=None)
    score_documents([doc], period, semantic_scores={doc.id: 0.5})
    assert doc.score_detail["no_body_penalty"] is False


def test_off_topic_still_zero_with_semantic(period):
    doc = make_doc(title="가을 제철 과일 신메뉴", body="식품업계 소식", snippet="")
    score_documents([doc], period, semantic_scores={doc.id: 0.95})
    assert doc.score == 0.0, "의미 유사도가 높아도 주제 게이트가 우선한다"

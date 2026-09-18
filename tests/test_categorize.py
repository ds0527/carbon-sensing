"""카테고리 분류: 설비/정책/기술/시장 판정과 비엄격 매칭의 필요성."""

from __future__ import annotations

from carbon_sensing.processing.categorize import DEFAULT_CATEGORY, assign_category
from tests.conftest import make_doc


def test_facility_category():
    doc = make_doc(
        title="포스코, 수소환원제철 실증설비 착공",
        body="탄소중립 목표로 실증설비를 착공했다",
    )
    assert assign_category(doc) == "설비"


def test_policy_category():
    doc = make_doc(
        title="EU CBAM, 철강 관세 부과 확정",
        body="탄소국경조정제도 시행령이 통과됐다",
    )
    assert assign_category(doc) == "정책"


def test_technology_category():
    doc = make_doc(
        title="현대제철, 수소환원제철 기술 특허 출원",
        body="탄소중립 R&D 기술 개발 성과",
    )
    assert assign_category(doc) == "기술"


def test_market_category():
    doc = make_doc(
        title="철강 가격 상승, 수출 계약 확대",
        body="탄소중립 비용이 가격에 반영됐다",
    )
    assert assign_category(doc) == "시장"


def test_compound_suffix_matches_despite_no_boundary():
    """'실증설비'처럼 '설비'가 어절 중간에 있어도 카테고리 판정은 잡아야 한다."""
    doc = make_doc(title="생산설비 신설 계획 발표", body="탄소중립 생산설비 투자")
    assert assign_category(doc) == "설비"


def test_no_category_keyword_falls_back_to_default():
    doc = make_doc(title="철강 탄소중립 간담회 개최", body="철강 탄소중립 논의", snippet="")
    assert assign_category(doc) == DEFAULT_CATEGORY


def test_tie_breaks_toward_policy():
    """정책·설비 키워드가 동수면 우선순위(정책>설비>기술>시장)를 따른다."""
    doc = make_doc(title="정책 설비", body="", snippet="")
    assert assign_category(doc) == "정책"

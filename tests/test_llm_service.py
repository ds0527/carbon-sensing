"""근거 검증(_ground): 없는 문서 id를 만들어내도 조용히 지우지 않는다."""

from __future__ import annotations

from carbon_sensing.llm.service import UNVERIFIED, _ground
from carbon_sensing.llm.schemas import Claim


def test_valid_source_is_verified():
    out = _ground([Claim(text="철강 감축 계획 발표", source_doc_id="abc")], {"abc"})
    assert len(out) == 1
    assert out[0].verified is True
    assert out[0].source_doc_id == "abc"
    assert UNVERIFIED not in out[0].text


def test_phantom_source_is_flagged_not_dropped():
    out = _ground([Claim(text="어디서 본 듯한 주장", source_doc_id="없는id")], {"abc"})
    assert len(out) == 1, "문장을 조용히 버리면 사용자가 문제를 못 본다"
    assert out[0].verified is False
    assert out[0].source_doc_id is None
    assert UNVERIFIED in out[0].text


def test_unknown_marker_is_flagged():
    out = _ground([Claim(text="확실치 않은 내용", source_doc_id="unknown")], {"abc"})
    assert out[0].verified is False
    assert UNVERIFIED in out[0].text


def test_empty_text_is_skipped():
    out = _ground(
        [
            Claim(text="   ", source_doc_id="abc"),
            Claim(text="실제 내용", source_doc_id="abc"),
        ],
        {"abc"},
    )
    assert len(out) == 1
    assert out[0].text == "실제 내용"


def test_mixed_claims_keep_order():
    claims = [
        Claim(text="첫째", source_doc_id="abc"),
        Claim(text="둘째", source_doc_id="가짜"),
        Claim(text="셋째", source_doc_id="abc"),
    ]
    out = _ground(claims, {"abc"})
    assert [g.text.split(" (")[0] for g in out] == ["첫째", "둘째", "셋째"]
    assert [g.verified for g in out] == [True, False, True]

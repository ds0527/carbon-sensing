"""키워드 매칭. 한글은 어절 경계를, 영문은 단어 경계를 본다.

단순 부분문자열 매칭은 한국어에서 오탐이 심하다.
  - "참고로"의 '고로'가 제철 고로로 잡힌다
  - "전기로 인한"의 '전기로'가 제강 전기로로 잡힌다
  - "제철 과일"의 '제철'이 제철소로 잡힌다(동음이의)
"""

from __future__ import annotations

import re

HANGUL_START = "가"
HANGUL_END = "힣"

# '~로'로 끝나는 키워드가 조사(수단·원인)로 쓰인 경우를 걸러낸다.
PARTICLE_TAILS = ("인해", "인한", "인하여", "써", "서")

_WS = re.compile(r"\s+")


def is_hangul(ch: str) -> bool:
    return bool(ch) and HANGUL_START <= ch <= HANGUL_END


def normalize(text: str) -> str:
    return _WS.sub(" ", (text or "")).strip().lower()


def count_hits(text: str, keyword: str, *, strict: bool = True) -> int:
    """text 안에서 keyword가 유효하게 등장한 횟수.

    strict=True(기본)는 어절 경계를 확인한다. 짧은 한글 키워드가 다른
    단어 속에 우연히 들어있는 오탐("참고로"의 '고로')을 막기 위한
    것으로, 철강/탄소 여부를 가르는 주제 게이트처럼 오탐이 치명적인
    곳에 쓴다.

    strict=False는 어절 경계를 보지 않는 단순 부분일치다. "실증설비"의
    '설비'처럼 정당한 복합어 접미사까지 걸러버리는 부작용이 있으므로,
    카테고리 분류처럼 오탐보다 미탐(recall 부족)이 더 나쁜 곳에 쓴다.
    """
    needle = (keyword or "").strip().lower()
    if not needle or not text:
        return 0
    if not strict:
        return text.count(needle)
    hits = 0
    pos = 0
    while True:
        idx = text.find(needle, pos)
        if idx < 0:
            return hits
        pos = idx + 1
        end = idx + len(needle)
        if is_hangul(needle[0]):
            # 어절 중간에서 시작하면 오탐이다: '참고로'의 '고로'
            if idx > 0 and is_hangul(text[idx - 1]):
                continue
            # '전기로 인한'처럼 조사로 쓰인 경우를 제외한다
            if needle.endswith("로"):
                tail = text[end:].lstrip()
                if tail.startswith(PARTICLE_TAILS):
                    continue
        else:
            # 영문은 앞쪽 경계만 본다(접두 키워드 'decarboni'가 'decarbonization'을 잡게).
            if idx > 0 and (text[idx - 1].isalnum() or text[idx - 1] == "-"):
                continue
        hits += 1


def match_keywords(
    text: str, keywords: list[str], *, strict: bool = True
) -> tuple[int, list[str]]:
    """(총 히트 수, 매칭된 키워드 목록)."""
    total = 0
    matched: list[str] = []
    for kw in keywords:
        n = count_hits(text, kw, strict=strict)
        if n:
            total += n
            matched.append(kw)
    return total, matched

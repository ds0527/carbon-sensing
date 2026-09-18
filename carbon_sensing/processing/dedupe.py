"""중복 제거. URL 정규화 + 제목 유사도 0.9로 병합한다."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..config import get_sources
from ..models import Document

TITLE_SIMILARITY_THRESHOLD = 0.9

# 추적 파라미터. 값이 달라도 같은 문서다.
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "_hs", "ref_")
TRACKING_KEYS = {
    "fbclid", "gclid", "dclid", "msclkid", "igshid", "yclid", "ref", "referrer",
    "spm", "cmpid", "cmp", "sc_campaign", "sc_channel", "sc_content", "sc_geo",
    "sc_outcome", "smid", "partner", "from", "source", "src", "share",
}
# 뉴스 사이트가 붙이는 흔한 문자열. 제목 비교 전에 벗긴다.
TITLE_NOISE_RE = re.compile(
    r"(\[[^\]]{1,20}\]|\([^)]{1,20}\)|<[^>]{1,20}>|【[^】]{1,20}】"
    r"|종합\s*\d*보?|속보|단독|영상|포토|사진|기고|칼럼|현장|인터뷰)",
    re.IGNORECASE,
)


def normalize_url(url: str) -> str:
    """추적 파라미터·프래그먼트·www·끝 슬래시를 정리한 비교용 URL."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    # 같은 문서가 http/https로 갈라져 들어오는 경우가 흔하므로 스킴은 통일한다.
    scheme = "https"
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parts.port and parts.port not in (80, 443):
        netloc = f"{host}:{parts.port}"
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in TRACKING_KEYS
        and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    query = urlencode(sorted(query_pairs))
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_title(title: str) -> str:
    """언론사 표기·괄호·기호를 벗긴 비교용 제목."""
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", title)
    text = TITLE_NOISE_RE.sub(" ", text)
    text = re.sub(r"[^0-9A-Za-z가-힣]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _trust(doc: Document) -> float:
    return get_sources().tier_of(doc.url)[1]


def _better(candidate: Document, current: Document) -> bool:
    """어느 쪽을 대표로 남길지. 신뢰도 > 본문 길이 > 게재일 이른 순."""
    c_key = (
        _trust(candidate),
        len(candidate.body or ""),
        -(candidate.published_at.timestamp() if candidate.published_at else 0),
    )
    k_key = (
        _trust(current),
        len(current.body or ""),
        -(current.published_at.timestamp() if current.published_at else 0),
    )
    return c_key > k_key


def _merge(keep: Document, drop: Document) -> Document:
    """대표 문서에 흡수 이력을 남긴다. duplicate_count는 언론사 수로 센다."""
    keep.merged_from.append((drop.source_name, drop.url))
    keep.merged_from.extend(drop.merged_from)
    sources = {keep.source_name} | {s for s, _ in keep.merged_from}
    keep.duplicate_count = max(1, len(sources))
    if not keep.has_body and drop.has_body:
        keep.body = drop.body
    if keep.published_at is None and drop.published_at is not None:
        keep.published_at = drop.published_at
    if len(drop.snippet) > len(keep.snippet):
        keep.snippet = drop.snippet
    for note in drop.notes:
        if note not in keep.notes:
            keep.notes.append(note)
    return keep


def dedupe(documents: list[Document]) -> list[Document]:
    """URL이 같거나 제목 유사도가 0.9 이상이면 한 건으로 병합한다.

    제목 비교는 같은 날짜(±1일) 문서끼리만 한다. 날짜가 멀리 떨어진
    문서는 제목이 비슷해도 후속 보도라 서로 다른 기사로 본다.
    """
    by_url: dict[str, Document] = {}
    for doc in documents:
        key = normalize_url(doc.url)
        existing = by_url.get(key)
        if existing is None:
            by_url[key] = doc
            continue
        if _better(doc, existing):
            by_url[key] = _merge(doc, existing)
        else:
            by_url[key] = _merge(existing, doc)

    survivors: list[Document] = []
    for doc in by_url.values():
        matched = None
        for other in survivors:
            if not _same_day_window(doc, other):
                continue
            if title_similarity(doc.title, other.title) >= TITLE_SIMILARITY_THRESHOLD:
                matched = other
                break
        if matched is None:
            survivors.append(doc)
            continue
        if _better(doc, matched):
            survivors[survivors.index(matched)] = _merge(doc, matched)
        else:
            _merge(matched, doc)
    return survivors


def _same_day_window(a: Document, b: Document, hours: int = 36) -> bool:
    if a.published_at is None or b.published_at is None:
        return True  # 일자 미확인끼리는 제목으로만 판단한다.
    return abs((a.published_at - b.published_at).total_seconds()) <= hours * 3600

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from carbon_sensing.daterange import build_range
from carbon_sensing.models import Document


@pytest.fixture
def period():
    from datetime import date

    return build_range("2026-09-01", "2026-09-15", today=date(2026, 9, 18))


def make_doc(
    title: str = "포스코, 수소환원제철 실증설비 착공",
    url: str = "https://www.yna.co.kr/view/AKR20260901",
    source_name: str = "yna.co.kr",
    published: str | None = "2026-09-05T09:00:00+09:00",
    body: str | None = "포스코가 수소환원제철 HyREX 실증설비를 착공했다. 탄소중립 목표.",
    region: str = "국내",
    doc_type: str = "기사",
    language: str = "ko",
    snippet: str = "포스코 수소환원제철 착공",
) -> Document:
    published_at = (
        datetime.fromisoformat(published).astimezone(timezone.utc) if published else None
    )
    return Document(
        title=title,
        url=url,
        source_name=source_name,
        published_at=published_at,
        language=language,
        region=region,
        doc_type=doc_type,
        snippet=snippet,
        body=body,
    )

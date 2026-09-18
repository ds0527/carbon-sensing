"""LLM 클라이언트: 토큰 예산, 캐시, 사용량 집계. API를 부르지 않는다."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from carbon_sensing.llm.client import (
    TokenBudgetExceeded,
    Usage,
    count_tokens,
    estimate_cost_usd,
)
from carbon_sensing.storage.db import Database


@pytest.fixture
def db():
    path = Path(tempfile.mkdtemp()) / "t.db"
    database = Database(path)
    yield database
    database.close()


def test_usage_accumulates_by_model():
    usage = Usage()
    usage.add("gpt-4o", 1000, 500)
    usage.add("gpt-4o", 500, 200)
    usage.add("gpt-4o-mini", 300, 100)
    assert usage.prompt_tokens == 1800
    assert usage.completion_tokens == 800
    assert usage.total_tokens == 2600
    assert usage.calls == 3
    assert usage.by_model["gpt-4o"]["calls"] == 2


def test_cost_reflects_model_pricing():
    cheap = estimate_cost_usd("gpt-4o-mini", 10000, 1000)
    pricey = estimate_cost_usd("gpt-4o", 10000, 1000)
    assert pricey > cheap > 0


def test_unknown_model_falls_back_to_default_pricing():
    assert estimate_cost_usd("some-future-model", 1000, 1000) > 0


def test_token_count_is_positive_for_korean():
    assert count_tokens("철강산업의 탄소중립 동향", "gpt-4o") > 0


def test_llm_cache_roundtrip(db):
    db.save_llm_response("key1", "gpt-4o", "ArticleSummary", '{"x": 1}')
    assert db.get_llm_response("key1") == '{"x": 1}'
    assert db.get_llm_response("missing") is None


def test_embedding_cache_roundtrip(db):
    db.save_embeddings("m1", [("h1", [0.1, 0.2]), ("h2", [0.3])])
    assert db.get_embedding("h1", "m1") == [0.1, 0.2]
    assert db.get_embedding("h1", "other-model") is None


class _FakeClient:
    """check_budget만 쓰는 최소 대역."""

    def __init__(self, max_tokens: int) -> None:
        self.max_tokens = max_tokens
        self.usage = Usage()

    check_budget = None  # 아래에서 실제 메서드를 붙인다


def test_budget_raises_when_exceeded():
    from carbon_sensing.llm.client import LlmClient

    fake = _FakeClient(max_tokens=1000)
    fake.usage.add("gpt-4o", 900, 50)
    # 바운드되지 않은 함수를 그대로 호출해 예산 로직만 검증한다.
    with pytest.raises(TokenBudgetExceeded):
        LlmClient.check_budget(fake, planned_tokens=200)


def test_budget_allows_within_limit():
    from carbon_sensing.llm.client import LlmClient

    fake = _FakeClient(max_tokens=10000)
    fake.usage.add("gpt-4o", 100, 50)
    LlmClient.check_budget(fake, planned_tokens=200)  # 예외 없음


def test_remaining_tokens():
    from carbon_sensing.llm.client import LlmClient

    fake = _FakeClient(max_tokens=1000)
    fake.usage.add("gpt-4o", 400, 100)
    assert LlmClient.remaining_tokens(fake) == 500

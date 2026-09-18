"""의미 유사도: 코사인, 본문 누락 패널티, 캐시 재사용."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from carbon_sensing.config import get_app_config
from carbon_sensing.scoring.embedding import (
    cosine,
    embed_input,
    semantic_scores,
    text_hash,
    topic_text,
)
from carbon_sensing.storage.db import Database
from tests.conftest import make_doc


class FakeLlm:
    """호출 횟수를 세는 임베딩 대역. 텍스트 길이로 벡터를 만든다."""

    def __init__(self, dim: int = 4) -> None:
        self.embedding_model = "fake-embed"
        self.calls = 0
        self.texts: list[str] = []
        self.dim = dim

    async def embed(self, texts: list[str]):
        self.calls += 1
        self.texts.extend(texts)
        out = []
        for t in texts:
            # '철강'이 있으면 주제 벡터와 같은 방향이 되게 만든다.
            base = [1.0, 0.0, 0.0, 0.0] if "철강" in t or "탄소" in t else [0.0, 1.0, 0.0, 0.0]
            out.append(base)
        return out


@pytest.fixture
def db():
    path = Path(tempfile.mkdtemp()) / "t.db"
    database = Database(path)
    yield database
    database.close()


def test_cosine_basics():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)


def test_cosine_handles_degenerate_input():
    assert cosine([], [1, 2]) == 0.0
    assert cosine([0, 0], [1, 2]) == 0.0
    assert cosine([1, 2, 3], [1, 2]) == 0.0


def test_topic_text_includes_extra_topics():
    text = topic_text(["CBAM"])
    assert "CBAM" in text
    assert "철강" in text


def test_embed_input_is_truncated():
    doc = make_doc(body="가" * 5000)
    assert len(embed_input(doc)) <= 1200


def test_embed_input_falls_back_to_snippet():
    doc = make_doc(body=None, snippet="발췌문만 있다")
    assert "발췌문만 있다" in embed_input(doc)


def test_semantic_scores_returns_none_without_client():
    result = asyncio.run(semantic_scores([make_doc()], None))
    assert result is None


def test_semantic_scores_returns_none_for_empty_docs():
    result = asyncio.run(semantic_scores([], FakeLlm()))
    assert result is None


def test_semantic_scores_in_unit_range(db):
    docs = [make_doc(url="https://a.kr/1"), make_doc(url="https://a.kr/2")]
    scores = asyncio.run(semantic_scores(docs, FakeLlm(), db=db))
    assert scores is not None
    assert all(0.0 <= v <= 1.0 for v in scores.values())


def test_no_body_document_gets_penalty(db):
    penalty = get_app_config().scoring.no_body_penalty
    with_body = make_doc(url="https://a.kr/1", body="철강 탄소중립 본문")
    without_body = make_doc(url="https://a.kr/2", body=None, snippet="철강 탄소중립 발췌")
    scores = asyncio.run(semantic_scores([with_body, without_body], FakeLlm(), db=db))
    assert scores[without_body.id] == pytest.approx(
        scores[with_body.id] * penalty, rel=1e-6
    )


def test_embeddings_are_cached_across_runs(db):
    docs = [make_doc(url="https://a.kr/1")]
    first = FakeLlm()
    asyncio.run(semantic_scores(docs, first, db=db))
    second = FakeLlm()
    asyncio.run(semantic_scores(docs, second, db=db))
    # 두 번째 실행은 주제·문서 벡터 모두 캐시에서 나와야 한다.
    assert second.calls == 0


def test_text_hash_is_stable():
    assert text_hash("같은 입력") == text_hash("같은 입력")
    assert text_hash("다른 입력") != text_hash("같은 입력")

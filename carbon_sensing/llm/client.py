"""OpenAI 호출 래퍼. 캐시·토큰예산·재시도를 한 곳에서 처리한다."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..config import get_settings
from ..storage.db import Database

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# 1K 토큰당 USD. 비용 표시는 참고값이며, 요금이 바뀌면 이 표만 고친다.
PRICING_PER_1K = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "text-embedding-3-small": (0.00002, 0.0),
    "text-embedding-3-large": (0.00013, 0.0),
}
DEFAULT_PRICING = (0.0025, 0.01)


class TokenBudgetExceeded(RuntimeError):
    """MAX_TOKENS_PER_RUN을 넘었을 때. 파이프라인은 여기서 멈춘다."""


@dataclass
class Usage:
    """실행 1회의 누적 사용량."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    cached_calls: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, model: str, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.calls += 1
        slot = self.by_model.setdefault(
            model, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        )
        slot["prompt_tokens"] += prompt
        slot["completion_tokens"] += completion
        slot["calls"] += 1

    def estimated_cost_usd(self) -> float:
        total = 0.0
        for model, slot in self.by_model.items():
            in_rate, out_rate = PRICING_PER_1K.get(model, DEFAULT_PRICING)
            total += slot["prompt_tokens"] / 1000 * in_rate
            total += slot["completion_tokens"] / 1000 * out_rate
        return round(total, 4)

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
            "cached_calls": self.cached_calls,
            "estimated_cost_usd": self.estimated_cost_usd(),
            "by_model": self.by_model,
        }


def count_tokens(text: str, model: str) -> int:
    """tiktoken이 있으면 정확히, 없으면 보수적으로 어림한다."""
    try:
        import tiktoken

        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            enc = tiktoken.get_encoding("o200k_base")
        return len(enc.encode(text))
    except Exception:
        # 한글은 대략 글자당 1토큰에 가깝다. 과소평가하지 않도록 넉넉히 잡는다.
        return len(text)


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_rate, out_rate = PRICING_PER_1K.get(model, DEFAULT_PRICING)
    return round(prompt_tokens / 1000 * in_rate + completion_tokens / 1000 * out_rate, 4)


class LlmClient:
    """구조화 출력 전용 클라이언트.

    - 응답은 항상 pydantic 스키마로 파싱한다.
    - 같은 (모델, 프롬프트, 스키마)면 SQLite 캐시에서 꺼내 API를 부르지 않는다.
    - 스키마 검증 실패는 1회 재시도하고, 그래도 안 되면 None을 반환하고 로그를 남긴다.
    - 누적 토큰이 MAX_TOKENS_PER_RUN을 넘으면 TokenBudgetExceeded를 던진다.
    """

    def __init__(self, db: Database | None = None, use_cache: bool = True) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError(".env에 OPENAI_API_KEY가 없습니다.")
        from openai import AsyncOpenAI

        kwargs: dict = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        self.client = AsyncOpenAI(**kwargs)
        self.settings = settings
        self.model = settings.openai_model
        self.model_light = settings.openai_model_light
        self.embedding_model = settings.openai_embedding_model
        self.max_tokens = settings.max_tokens_per_run
        self.usage = Usage()
        self.db = db
        self.use_cache = use_cache and db is not None
        # 프롬프트를 바꾸면 캐시가 무효화되어야 한다.
        self.prompt_version = "v2"

    def check_budget(self, planned_tokens: int = 0) -> None:
        projected = self.usage.total_tokens + planned_tokens
        if projected > self.max_tokens:
            raise TokenBudgetExceeded(
                f"토큰 예산 초과: 누적 {self.usage.total_tokens:,} + 예정 "
                f"{planned_tokens:,} > 상한 {self.max_tokens:,}. "
                f"MAX_TOKENS_PER_RUN을 올리거나 --top-k를 줄이세요."
            )

    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.usage.total_tokens)

    def _cache_key(self, model: str, system: str, user: str, schema_name: str) -> str:
        payload = json.dumps(
            {
                "v": self.prompt_version,
                "model": model,
                "system": system,
                "user": user,
                "schema": schema_name,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> T | None:
        """구조화 응답 1건. 실패하면 None을 반환하고 파이프라인은 계속 간다."""
        model = model or self.model
        key = self._cache_key(model, system, user, schema.__name__)

        if self.use_cache and self.db is not None:
            cached = self.db.get_llm_response(key)
            if cached:
                try:
                    parsed = schema.model_validate_json(cached)
                    self.usage.cached_calls += 1
                    log.info("LLM 캐시 적중: %s / %s", model, schema.__name__)
                    return parsed
                except ValidationError:
                    log.debug("캐시 스키마 불일치, 무시: %s", schema.__name__)

        planned = count_tokens(system + user, model) + 800
        self.check_budget(planned)

        for attempt in (1, 2):
            try:
                resp = await self.client.chat.completions.parse(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format=schema,
                    temperature=temperature,
                )
            except Exception as exc:
                log.warning(
                    "LLM 호출 실패(%s, 시도 %d/2): %s", schema.__name__, attempt, exc
                )
                if attempt == 2:
                    return None
                continue

            if resp.usage:
                self.usage.add(
                    model, resp.usage.prompt_tokens, resp.usage.completion_tokens
                )

            parsed = resp.choices[0].message.parsed
            if parsed is None:
                refusal = getattr(resp.choices[0].message, "refusal", None)
                log.warning(
                    "LLM 파싱 실패(%s, 시도 %d/2): refusal=%s",
                    schema.__name__,
                    attempt,
                    refusal,
                )
                if attempt == 2:
                    return None
                continue

            if self.use_cache and self.db is not None:
                self.db.save_llm_response(
                    key, model, schema.__name__, parsed.model_dump_json()
                )
            return parsed
        return None

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        """임베딩 배치. 문서 단위 캐시는 scoring/embedding.py가 관리한다."""
        if not texts:
            return []
        planned = sum(count_tokens(t, self.embedding_model) for t in texts)
        self.check_budget(planned)
        try:
            resp = await self.client.embeddings.create(
                model=self.embedding_model, input=texts
            )
        except Exception as exc:
            log.warning("임베딩 호출 실패: %s", exc)
            return None
        if resp.usage:
            self.usage.add(self.embedding_model, resp.usage.prompt_tokens, 0)
        return [item.embedding for item in resp.data]

    async def aclose(self) -> None:
        try:
            await self.client.close()
        except Exception:
            pass


def build_client(db: Database | None = None, use_cache: bool = True) -> LlmClient | None:
    """키가 없으면 None. 호출부는 None이면 LLM 단계를 건너뛴다."""
    if not get_settings().openai_api_key:
        return None
    try:
        return LlmClient(db=db, use_cache=use_cache)
    except Exception as exc:
        log.warning("LLM 클라이언트 생성 실패: %s", exc)
        return None

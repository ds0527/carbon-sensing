"""설정 로드와 검증. 값은 .env(비밀)와 *.yaml(정책)으로 나눈다."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


def ensure_utf8_stdout() -> None:
    """Windows 콘솔에서 한글이 깨지지 않도록 출력 인코딩을 맞춘다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


class Settings(BaseSettings):
    """.env에서만 읽는 값. 키와 모델명을 코드에 하드코딩하지 않는다."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    naver_client_id: str | None = None
    naver_client_secret: str | None = None
    tavily_api_key: str | None = None

    # 2단계에서 사용. 1단계에서는 읽기만 하고 호출하지 않는다.
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    openai_model_light: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_base_url: str | None = None
    max_tokens_per_run: int = 200_000

    # HTTP 헤더는 ASCII만 허용된다. 한글을 넣으면 요청 자체가 실패한다.
    user_agent: str = (
        "CarbonSensingBot/0.1 (carbon-neutrality trend research; contact: set-your-email)"
    )

    @field_validator("user_agent")
    @classmethod
    def _ascii_only_user_agent(cls, value: str) -> str:
        """비ASCII 문자가 섞이면 httpx가 헤더를 만들지 못하므로 미리 걸러낸다."""
        cleaned = value.encode("ascii", "ignore").decode("ascii").strip()
        return cleaned or "CarbonSensingBot/0.1"

    @property
    def naver_ready(self) -> bool:
        return bool(self.naver_client_id and self.naver_client_secret)

    @property
    def tavily_ready(self) -> bool:
        return bool(self.tavily_api_key)

    def missing_keys(self) -> dict[str, list[str]]:
        """수집기별로 비어 있는 .env 변수명. 키 값 자체는 노출하지 않는다."""
        missing: dict[str, list[str]] = {}
        if not self.naver_client_id or not self.naver_client_secret:
            missing["naver_news"] = [
                name
                for name, value in (
                    ("NAVER_CLIENT_ID", self.naver_client_id),
                    ("NAVER_CLIENT_SECRET", self.naver_client_secret),
                )
                if not value
            ]
        if not self.tavily_api_key:
            missing["web_search"] = ["TAVILY_API_KEY"]
        return missing


class ScoringWeights(BaseModel):
    """PRD 6장의 가중치. 1단계에서 semantic은 0으로 두고 나머지를 재정규화한다."""

    semantic: float = 0.40
    keyword: float = 0.20
    source_trust: float = 0.20
    recency: float = 0.10
    spread: float = 0.10

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


class ScoringConfig(BaseModel):
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    min_score_to_show: float = 40.0
    title_weight: float = 2.0
    body_weight: float = 1.0
    no_body_penalty: float = 0.85
    list_size: int = 30


class CollectConfig(BaseModel):
    per_query_limit: int = 50
    max_queries: int = 12
    concurrency: int = 5
    domain_delay_seconds: float = 1.0
    request_timeout_seconds: float = 20.0
    max_retries: int = 3
    body_fetch_limit: int = 60
    respect_robots: bool = True
    long_period_warning_days: int = 90


class ReportConfig(BaseModel):
    top_n: int = 5
    body_char_limit: int = 4000


class AppConfig(BaseModel):
    """config.yaml 전체."""

    collect: CollectConfig = Field(default_factory=CollectConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)


class SourceTiers(BaseModel):
    """sources.yaml: 도메인별 신뢰도와 RSS 피드 목록."""

    tier_values: dict[str, float] = Field(
        default_factory=lambda: {
            "논문_기관": 1.0,
            "주요_전문": 0.8,
            "일반": 0.6,
            "미분류": 0.3,
        }
    )
    domains: dict[str, str] = Field(default_factory=dict)
    feeds: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("domains", mode="before")
    @classmethod
    def _lower_domains(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k).lower().lstrip("."): v for k, v in value.items()}
        return value

    def tier_of(self, url: str) -> tuple[str, float]:
        """URL의 신뢰도 등급과 점수. 서브도메인은 상위 도메인 규칙을 물려받는다."""
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        parts = host.split(".") if host else []
        for i in range(len(parts)):
            candidate = ".".join(parts[i:])
            tier = self.domains.get(candidate)
            if tier:
                return tier, self.tier_values.get(tier, self.tier_values["미분류"])
        return "미분류", self.tier_values.get("미분류", 0.3)


class TopicConfig(BaseModel):
    """topics.yaml: 고정 기본 주제와 저장된 프로필."""

    base_topic: str = "철강산업의 탄소중립"
    base_queries_ko: list[str] = Field(default_factory=list)
    base_queries_en: list[str] = Field(default_factory=list)
    steel_keywords: list[str] = Field(default_factory=list)
    carbon_keywords: list[str] = Field(default_factory=list)
    bonus_keywords: list[str] = Field(default_factory=list)

    @property
    def all_keywords(self) -> list[str]:
        return self.steel_keywords + self.carbon_keywords + self.bonus_keywords
    default_excludes: list[str] = Field(default_factory=list)
    category_keywords: dict[str, list[str]] = Field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = Field(default_factory=dict)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return AppConfig.model_validate(_read_yaml(PACKAGE_DIR / "config.yaml"))


@lru_cache(maxsize=1)
def get_sources() -> SourceTiers:
    return SourceTiers.model_validate(_read_yaml(PACKAGE_DIR / "sources.yaml"))


@lru_cache(maxsize=1)
def get_topics() -> TopicConfig:
    return TopicConfig.model_validate(_read_yaml(PACKAGE_DIR / "topics.yaml"))


def reset_config_cache() -> None:
    """테스트에서 yaml을 바꿔 끼울 때 사용."""
    for fn in (get_settings, get_app_config, get_sources, get_topics):
        fn.cache_clear()

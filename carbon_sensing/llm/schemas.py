"""LLM 구조화 출력 스키마. 모든 응답은 이 중 하나로 파싱된다."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Claim(BaseModel):
    """근거가 있는 문장 하나. source_doc_id는 반드시 컨텍스트로 준 문서 id여야 한다."""

    text: str = Field(description="한 문장. 원문에 없는 사실을 넣지 않는다.")
    source_doc_id: str = Field(
        description="이 문장의 근거가 된 문서 id. 확실치 않으면 'unknown'."
    )


class QueryExpansion(BaseModel):
    """추가 주제를 검색 질의어로 확장한 결과."""

    queries: list[str] = Field(description="3~5개의 한글·영문 검색 질의어")


class RepresentativePick(BaseModel):
    doc_id: str
    reason: str = Field(description="이 문서를 대표로 고른 이유, 한 문장")


class RepresentativeSelection(BaseModel):
    """상위 후보 중 대표 기사로 고른 것들."""

    picks: list[RepresentativePick]


class ArticleSummary(BaseModel):
    """기사 한 건의 요약과 시사점."""

    summary: list[Claim] = Field(description="3~5문장 요약")
    implications: list[Claim] = Field(description="2~3개 시사점")


class SynthesisResult(BaseModel):
    """기간 전체를 종합한 결과(1장 Summary와 리포트 종합 섹션에 쓰인다)."""

    headline: str = Field(description="기간 전체를 관통하는 헤드라인 한 문장")
    trends: list[Claim] = Field(description="핵심 트렌드 3~5개")
    implications: list[Claim] = Field(description="종합 시사점 3개")

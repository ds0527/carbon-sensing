"""프롬프트. 역할·근거 강제·수치 인용 규칙을 한 곳에 모은다."""

from __future__ import annotations

from ..models import Document

SYSTEM = """당신은 철강산업 탄소중립 담당 애널리스트다.
독자는 철강사 경영진이다. 과장 없는 문체로, 짧고 사실만 쓴다.

반드시 지킬 것:
1) 입력으로 준 문서 본문에 없는 사실은 절대 쓰지 않는다. 배경지식으로 추론하지 않는다.
2) 확실하지 않으면 그 문장을 쓰지 말고, 꼭 필요하면 '원문 미확인'이라고 적는다.
3) 수치(감축량, 투자액, 연도, 비율)는 원문 표현 그대로 인용하고 단위를 병기한다.
   원문에 없는 수치를 만들지 않는다.
4) 모든 문장에는 근거가 된 문서 id를 source_doc_id로 달아 반환한다.
   준 적 없는 id를 쓰지 않는다. 근거가 불분명하면 'unknown'을 쓴다.
5) 한국어로 쓴다. 해외 기사도 한국어로 요약하고 고유명사는 원문을 병기한다.
6) 문장은 25자 이상 60자 내외로 간결하게. 형용사보다 숫자·기관명·날짜를 쓴다.
7) 시사점(implications)은 뉴스를 되풀이하는 문장이 아니다. 국내 철강사 입장에서 원가·관세·설비투자·
   기술격차·규제 대응기한·경쟁구도 중 무엇에 어떻게 노출되는지를, 원문에 있는 조치·수치·시점을 근거로
   구체적으로 쓴다. '영향이 있을 것으로 보인다', '주목된다' 같은 뭉뚱그린 표현은 쓰지 않는다."""

CATEGORY_GUIDE = """카테고리는 다음 중 하나로 본다:
- 설비: 착공·준공·증설·투자·가동 등 물리적 설비와 자본지출
- 정책: 규제·법안·배출권·CBAM·보조금 등 제도
- 기술: R&D·실증·특허·수소환원제철·CCUS 등 기술 개발
- 시장: 가격·수요·공급·실적·계약 등 시장 동향"""


def doc_block(doc: Document, body_limit: int) -> str:
    """LLM에 넘기는 문서 한 건의 표현. 본문은 앞부분+결론부를 잘라 넣는다."""
    body = (doc.body or doc.snippet or "").strip()
    if len(body) > body_limit:
        head = body[: int(body_limit * 0.7)]
        tail = body[-int(body_limit * 0.3) :]
        body = f"{head}\n...(중략)...\n{tail}"
    date = doc.published_at.strftime("%Y-%m-%d") if doc.published_at else "일자미확인"
    return (
        f"[문서 id: {doc.id}]\n"
        f"제목: {doc.title}\n"
        f"출처: {doc.source_name} ({doc.region}, {doc.doc_type}, 카테고리 {doc.category})\n"
        f"게재일: {date}\n"
        f"본문: {body if body else '(본문 없음 — 제목과 발췌문만으로 판단하고, 부족하면 원문 미확인으로 적을 것)'}"
    )


def expand_queries_user(extra_topics: list[str], base_topic: str) -> str:
    return (
        f"기본 주제는 '{base_topic}'이다.\n"
        f"사용자가 추가한 주제: {', '.join(extra_topics)}\n\n"
        "이 추가 주제를 뉴스·보고서·논문 검색에 쓸 질의어로 확장해라.\n"
        "- 한글 질의어와 영문 질의어를 섞어 3~5개만 만든다.\n"
        "- 각 질의어는 기본 주제(철강 탄소중립)와 추가 주제가 함께 걸리도록 구성한다.\n"
        "- 너무 일반적인 단어('뉴스', '동향')만으로 된 질의어는 만들지 않는다."
    )


def select_representative_user(
    docs: list[Document], top_n: int, body_limit: int
) -> str:
    blocks = "\n\n".join(doc_block(d, min(body_limit, 800)) for d in docs)
    return (
        f"아래는 관련도 순 상위 후보 문서 {len(docs)}건이다.\n\n{blocks}\n\n"
        f"이 중 대표 기사 {top_n}건을 골라라.\n"
        "선정 기준:\n"
        "- 관련도(철강 탄소중립 핵심 사안인가)\n"
        "- 출처 신뢰도(기관·논문 > 주요·전문 언론 > 일반)\n"
        "- 이슈 확산도(여러 언론이 같이 다룬 사안인가)\n"
        f"- 카테고리 다양성: 가능하면 설비·정책·기술·시장이 골고루 들어가게 한다\n"
        "- 국내·해외 밸런스: 최소 1건은 해외 소스\n"
        "- 같은 이슈가 중복되면 한 건만 대표로 고른다\n\n"
        f"{CATEGORY_GUIDE}\n\n"
        "doc_id는 위에 준 문서 id 중에서만 고른다. 없는 id를 만들지 않는다."
    )


def summarize_user(doc: Document, body_limit: int) -> str:
    return (
        f"{doc_block(doc, body_limit)}\n\n"
        "이 문서를 요약하고 시사점을 써라.\n"
        "- summary: 3~5문장. 무엇이 일어났는지, 수치와 주체를 포함해서.\n"
        "- implications: 2~3개. 국내 철강사가 이 뉴스로 무엇에 노출되는지 구체적으로 쓴다\n"
        "  (예: 원가·관세 부담, 설비투자 필요성, 경쟁사 대비 기술격차, 규제 대응기한).\n"
        "  '중요하다', '주목된다', '영향이 있을 것으로 보인다' 같은 공허한 문장은 쓰지 않는다.\n"
        "  원문에 근거가 있을 때만 구체적 수치·시점·대상을 적는다.\n"
        f"- 모든 문장의 source_doc_id는 '{doc.id}'로 한다.\n"
        "- 본문에 없는 내용은 쓰지 않는다."
    )


def synthesize_user(
    docs_with_summaries: list[tuple[Document, list[str], list[str]]],
    period_label: str,
    category_counts: dict[str, int],
) -> str:
    parts = []
    for doc, summary_lines, implication_lines in docs_with_summaries:
        date = doc.published_at.strftime("%Y-%m-%d") if doc.published_at else "일자미확인"
        parts.append(
            f"[문서 id: {doc.id}] ({doc.category}) {doc.title} — {doc.source_name}, {date}\n"
            f"  요약: {' '.join(summary_lines)}\n"
            f"  시사점: {' / '.join(implication_lines)}"
        )
    blocks = "\n\n".join(parts)
    dist = ", ".join(f"{k} {v}건" for k, v in category_counts.items()) or "집계 없음"
    return (
        f"기간: {period_label}\n"
        f"카테고리 분포: {dist}\n\n"
        f"아래는 이 기간의 대표 기사와 각각의 요약·시사점이다.\n\n{blocks}\n\n"
        "이 기간 전체를 종합해라.\n"
        "- headline: 기간을 관통하는 메시지 한 문장.\n"
        "- trends: 핵심 트렌드 3~5개. 각 트렌드는 어느 카테고리(설비/정책/기술/시장)의\n"
        "  움직임인지 문장 앞에 '[설비]' 같은 형태로 밝히고 시작한다.\n"
        "- implications: 종합 시사점 3개. 사업 관점 1개, 기술 관점 1개, 정책 관점 1개로\n"
        "  나눠서 각 문장 앞에 '[사업]', '[기술]', '[정책]'을 붙인다.\n"
        "  각 문장은 국내 철강사가 무엇을 준비·대응해야 하는지 구체적으로 쓴다. 막연한 전망이 아니라\n"
        "  원문에 있는 조치·수치·시점을 근거로 든다.\n\n"
        f"{CATEGORY_GUIDE}\n\n"
        "위에 준 문서 id만 source_doc_id로 쓴다. 여러 문서를 묶는 문장은 가장 대표적인 id 하나를 쓴다."
    )

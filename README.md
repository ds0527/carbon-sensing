# 철강산업 탄소중립 동향 센싱 (3단계)

기간과 주제를 넣으면 국내외 기사·보도자료·보고서·논문을 수집해 관련도 순으로
정렬하고, 대표 기사를 골라 요약·시사점과 **1장짜리 Summary**까지 만드는 도구입니다.
Streamlit 웹 UI와 CLI 둘 다 지원하며, 같은 파이프라인을 공유합니다.

1장 Summary는 상세 리포트를 대체하지 않고 **별도로 추가 생성**됩니다.

## 준비

```
python -m pip install -r requirements.txt
python -m playwright install chromium   # PDF 생성용
copy .env.example .env
```

`.env`에 쓸 키를 채웁니다. 키가 없는 수집기·단계는 건너뛰고 사유를 화면·리포트에
남깁니다(중단되지 않습니다).

| 변수 | 용도 | 없으면 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 요약·시사점·대표기사 선정·임베딩 | LLM 단계 전체 건너뜀 (목록까지는 생성) |
| `OPENAI_MODEL` | 요약·종합용 모델 (기본 `gpt-4o`) | 기본값 사용 |
| `OPENAI_MODEL_LIGHT` | 질의어 확장 등 경작업 (기본 `gpt-4o-mini`) | 기본값 사용 |
| `OPENAI_EMBEDDING_MODEL` | 의미 유사도 (기본 `text-embedding-3-small`) | 기본값 사용 |
| `MAX_TOKENS_PER_RUN` | 1회 실행 토큰 상한 (기본 200,000) | 기본값 사용 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | 네이버 뉴스 검색 | naver_news 건너뜀 |
| `TAVILY_API_KEY` | 국내외 범용 웹 검색 | web_search 건너뜀 |

`openalex`(논문), `gdelt`(해외뉴스), `rss`(기관 피드)는 키가 필요 없습니다.

## 실행 — 웹 UI (권장)

```
streamlit run app.py
```

브라우저가 열리면:

1. 사이드바에서 **기간**(프리셋 또는 직접 입력), **추가 주제**, 제외어, 수집
   소스, 대표 기사 건수를 정합니다. 예상 토큰·비용이 실행 전에 표시됩니다.
2. **동향 분석 시작**을 누르면 단계별 진행 상황이 실시간으로 보입니다.
3. **Summary** 탭에서 카테고리별 대표 기사와 종합 시사점을 바로 봅니다.
4. **문서 목록** 탭에서 전체 문서를 필터·정렬하고, 대표 기사 체크박스를 직접
   바꿔 **이 선택으로 다시 생성**을 누르면 수집 없이 요약만 다시 만듭니다.
5. **다운로드** 탭에서 Markdown·PDF·HTML·Excel·JSON 8종을 받습니다.
6. **실행 이력** 탭에서 과거 실행을 다시 불러옵니다(API 호출 없음).

## 실행 — CLI

```
python -m carbon_sensing.main run --from 2026-09-01 --to 2026-09-15
python -m carbon_sensing.main run --preset 30d --topic "CBAM" --topic "배출권"
python -m carbon_sensing.main run --preset 7d --mode or --top-k 7
python -m carbon_sensing.main run --preset 30d --no-llm          # 목록만
python -m carbon_sensing.main run --preset 30d --collector openalex --collector gdelt
```

수집을 다시 하지 않고 리포트만 고쳐 뽑기:

```
python -m carbon_sensing.main rerun outputs/2026-09-18_1137
python -m carbon_sensing.main rerun outputs/2026-09-18_1137 --pick <문서id> --pick <문서id>
```

기타: `check`(.env·설정 점검), `presets`(기간 프리셋 목록).

## 배포 — Streamlit Community Cloud

인터넷 어디서든 브라우저로 접속하게 하려면 GitHub 저장소로 올린 뒤
[share.streamlit.io](https://share.streamlit.io)에서 앱을 만들면 됩니다(Main file
path: `app.py`).

`.env`는 `.gitignore`에 걸려 있어 저장소에 올라가지 않으므로, 배포된 앱의
**Settings → Secrets**에 `.env`와 같은 키 이름으로 TOML 형식으로 등록합니다:

```
OPENAI_API_KEY = "sk-..."
NAVER_CLIENT_ID = "..."
NAVER_CLIENT_SECRET = "..."
TAVILY_API_KEY = "..."
```

`app.py` 맨 위에서 `st.secrets`를 환경변수로 옮겨주므로 `Settings()`가 그대로
읽습니다.

알아둘 제약:
- Streamlit Cloud 컨테이너엔 Chromium이 없고 `playwright install chromium`이
  자동 실행되지 않습니다. PDF 생성만 조용히 건너뛰고(`report/markdown_renderer.py`
  가 실패를 잡아 로그만 남김) Markdown·HTML·Excel·JSON은 정상 생성됩니다.
- `outputs/`와 `carbon_sensing.db`는 컨테이너 안에만 있어 재시작·재배포 시
  초기화됩니다 — 실행 이력과 캐시가 유지되지 않고, 재실행 시 API를 다시 호출합니다.

## 결과물

`outputs/<실행시각>/` 아래에 8개 파일이 생깁니다.

| 파일 | 내용 |
| --- | --- |
| `summary.md` / `.html` / `.pdf` | **1장짜리 Summary.** 헤드라인 → 카테고리별 핵심 트렌드 → 카테고리별 대표 기사 → 시사점. PDF는 실제 렌더링 높이를 측정해 A4 한 장에 자동으로 맞춥니다. |
| `report.md` / `.html` / `.pdf` | 상세 리포트. 실행조건·질의어·수집현황·관련도 순 전체 목록·대표기사 요약/시사점·종합·부록 |
| `articles.xlsx` | 전체 문서 목록(필터·정렬 가능) + 실행조건 + 수집기 현황 3개 시트 |
| `raw.json` | 원본 데이터, 점수 상세, LLM 응답, 사용량. `rerun`과 UI의 "실행 이력"이 이 파일을 읽습니다 |

## 디자인

리포트(HTML/PDF)와 UI는 같은 **듀오톤 디자인 시스템**을 씁니다(잉크 네이비 +
블루 액센트 두 톤). 16:9 히어로 배너, 숫자 밴드, 카테고리 칩(채움/외곽선/농도/
파선으로 구분)을 공유합니다. 화면은 다크, 인쇄는 토너를 아끼는 라이트 반전
버전을 자동으로 씁니다(`carbon_sensing/report/templates/base.css`).

## 카테고리

모든 문서는 **설비 / 정책 / 기술 / 시장 / 기타**로 분류됩니다. 규칙 기반이라
LLM 비용이 들지 않습니다. 분류 키워드는 `topics.yaml`의 `category_keywords`에서
고칩니다.

## 관련도 점수

PRD 6장의 5개 지표를 0~100으로 합산합니다.

| 지표 | 가중치 | 비고 |
| --- | --- | --- |
| 의미 유사도 | 40% | 주제 설명문과의 임베딩 코사인 유사도 |
| 키워드 적합도 | 20% | 제목 ×2.0, 본문 ×1.0 |
| 출처 신뢰도 | 20% | `sources.yaml`의 도메인 등급 |
| 최신성 | 10% | 기간 내 상대 위치 |
| 이슈 확산도 | 10% | 중복보도 언론사 수의 log |

`OPENAI_API_KEY`가 없으면 의미 유사도를 빼고 나머지 4개를 재정규화합니다.

**주제 게이트**: 철강 키워드와 탄소 키워드가 **모두** 걸려야 주제 문서로
인정합니다. 하나라도 없으면 0점 처리합니다.

## 비용 관리

- UI 사이드바와 실행 종료 시 실제 토큰 사용량·추정 비용(USD)을 보여줍니다.
- `MAX_TOKENS_PER_RUN`을 넘을 것 같으면 **호출 전에** 중단합니다.
- LLM 응답·임베딩·본문은 SQLite에 캐시됩니다. 같은 조건 재실행은 API를 호출하지
  않습니다.

## 테스트

```
python -m pytest tests -q
```

## 설정 파일

| 파일 | 내용 |
| --- | --- |
| `carbon_sensing/topics.yaml` | 기본 주제 질의어, 철강/탄소/보너스/카테고리 키워드, 제외어, 주제 프로필 |
| `carbon_sensing/sources.yaml` | 도메인별 신뢰도 등급, RSS 피드 목록 |
| `carbon_sensing/config.yaml` | 수집 동시성·재시도, 스코어링 가중치·임계값, 대표기사 건수 |
| `.streamlit/config.toml` | UI 테마(듀오톤) |

## 알려진 제약

- 기관 보고서 전용 수집기(`report_sites`)는 아직 없습니다. `rss` 피드에 기관
  URL을 추가해 대신 씁니다.
- 일부 사이트는 봇 차단(403)이나 유료 장벽으로 본문을 못 가져옵니다. 이 경우
  제목·발췌문만으로 판단하고 의미 유사도에 패널티를 적용합니다.
- PDF는 Playwright Chromium을 씁니다. 최초 실행 전 `playwright install chromium`이
  필요합니다.

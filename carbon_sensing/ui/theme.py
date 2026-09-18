"""Streamlit에 주입하는 라이트 듀오톤 CSS. 리포트와 같은 두 톤(잉크+블루)을 쓴다."""

from __future__ import annotations

INK_900 = "#0B1424"
INK_800 = "#122036"
INK_700 = "#1B2C47"
INK_600 = "#E2E8F2"
BG = "#F5F8FC"
SURFACE = "#FFFFFF"
DUO_500 = "#2563EB"
DUO_400 = "#3B82F6"
DUO_DIM = "rgba(37, 99, 235, 0.55)"
DUO_FAINT = "rgba(37, 99, 235, 0.08)"
FG = "#0F172A"
FG_MUTED = "#5B6B82"

# 카테고리는 듀오톤 안에서 채움/외곽선/농도/파선으로만 구분한다.
CATEGORY_STYLE = {
    "정책": ("fill", DUO_500),
    "설비": ("outline", DUO_500),
    "기술": ("dim", DUO_DIM),
    "시장": ("dash", DUO_DIM),
    "기타": ("dim", FG_MUTED),
}

CSS = f"""
<style>
  /* ── 라이트 듀오톤 기본 ── */
  .stApp {{ background: {BG}; }}
  .block-container {{ padding-top: 1.2rem; max-width: 1400px; }}

  h1, h2, h3 {{ letter-spacing: -0.03em; }}
  /* 기본 텍스트 색은 .streamlit/config.toml의 textColor가 이미 준다.
     여기서 span/div까지 블랭킷으로 덮으면, Streamlit이 헤딩(h1)마다
     자동으로 넣는 앵커링크용 내부 <span>까지 걸려 색이 바뀐다 — 부모
     h1에 흰색을 줘도 그 span 자신의 색 규칙이 우선하기 때문에, 히어로
     제목이 흰색 대신 이 어두운 기본색으로 보이는 버그로 실제 재현됐다. */

  /* 16:9 히어로 배너 — 밝은 페이지 위에 놓이는 진한 블루 배너. 유일하게 잉크 톤을 쓰는 자리. */
  /* Spotify 커버류 듀오톤 포스터: 부드러운 그라디언트 대신 두 색을 뚜렷한
     경계로 나눈다. 텍스트는 항상 잉크 블록(왼쪽) 안에만 두어, 그라디언트의
     중간 전이 구간과 겹쳐 대비가 무너지는 일이 구조적으로 생기지 않는다. */
  .cs-hero {{
    position: relative;
    width: 100%;
    aspect-ratio: 16 / 9;
    max-height: 210px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    padding: 26px 30px 22px;
    margin-bottom: 20px;
    border-bottom: 2px solid {DUO_500};
    background: linear-gradient(
      104deg,
      {INK_900} 0%, {INK_900} 62%,
      {DUO_500} 62%, {DUO_500} 100%
    );
  }}
  .cs-hero::after {{
    /* 액센트 블록(오른쪽)에만 얹는 사선 하프톤 질감 — 포스터 인쇄 느낌 */
    content: "";
    position: absolute;
    inset: 0;
    background: repeating-linear-gradient(
      58deg, rgba(255, 255, 255, 0.14) 0 1.2px, transparent 1.2px 8px);
    -webkit-mask-image: linear-gradient(104deg, transparent 62%, #000 62%);
    mask-image: linear-gradient(104deg, transparent 62%, #000 62%);
  }}
  .cs-hero > * {{ position: relative; z-index: 1; }}
  /* 색은 !important로 고정한다. Streamlit이 헤더(h1)에 자체 클래스를
     주입하면서 특정 상황에만 이 규칙과 우선순위가 같아져 검게 보이는
     경우가 실제로 있었다(views.hero()의 인라인 색상과 이중 방어). */
  .cs-hero .kicker {{
    font-size: 11px; font-weight: 800; letter-spacing: 0.24em;
    text-transform: uppercase; color: #93C5FD !important; margin-bottom: 10px;
    text-shadow: 0 1px 6px rgba(0, 0, 0, 0.5);
  }}
  .cs-hero h1 {{
    margin: 0; font-size: 34px; font-weight: 800; line-height: 1.08;
    letter-spacing: -0.04em; color: #FFFFFF !important; max-width: 26ch;
    text-shadow: 0 2px 10px rgba(0, 0, 0, 0.5), 0 1px 3px rgba(0, 0, 0, 0.6);
  }}
  .cs-hero .sub {{
    margin-top: 12px; font-size: 13px; color: rgba(233, 239, 247, 0.78) !important;
    text-shadow: 0 1px 4px rgba(0, 0, 0, 0.45);
  }}

  /* 숫자 밴드 */
  .cs-band {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
    border-top: 1px solid {DUO_DIM}; border-bottom: 1px solid {DUO_DIM};
    margin-bottom: 18px;
  }}
  .cs-band .cell {{ padding: 12px 0 12px 14px; }}
  .cs-band .cell + .cell {{ border-left: 1px solid {INK_600}; }}
  .cs-band .n {{
    display: block; font-size: 26px; font-weight: 800; color: {DUO_500};
    line-height: 1; letter-spacing: -0.04em;
  }}
  .cs-band .k {{
    display: block; margin-top: 6px; font-size: 10px; letter-spacing: 0.16em;
    text-transform: uppercase; color: {FG_MUTED};
  }}

  /* 카테고리 칩 */
  .cs-chip {{
    display: inline-block; font-size: 10px; font-weight: 800;
    letter-spacing: 0.14em; padding: 2px 8px; border: 1px solid {DUO_500};
    color: {DUO_500}; margin-right: 6px;
  }}
  .cs-chip.fill {{ background: {DUO_500}; color: #FFFFFF; }}
  .cs-chip.dim {{ border-color: {DUO_DIM}; color: {DUO_400}; }}
  .cs-chip.dash {{ border-style: dashed; border-color: {DUO_DIM}; color: {DUO_400}; }}

  /* 섹션 제목 */
  .cs-h2 {{
    font-size: 12px; font-weight: 800; letter-spacing: 0.2em;
    text-transform: uppercase; color: {DUO_500};
    border-bottom: 2px solid {DUO_500}; padding-bottom: 6px; margin: 22px 0 12px;
  }}

  /* 대표 기사 카드 */
  .cs-art {{
    border-left: 2px solid {DUO_DIM}; padding: 2px 0 2px 14px; margin-bottom: 16px;
  }}
  .cs-art .t {{ font-size: 15px; font-weight: 700; letter-spacing: -0.02em; }}
  .cs-art .t a {{ color: {FG}; text-decoration: none; border-bottom: 1px solid {DUO_DIM}; }}
  .cs-art .src {{ font-size: 11px; color: {FG_MUTED}; margin-top: 3px; }}
  .cs-cite {{ font-size: 10px; font-weight: 800; color: {DUO_500}; vertical-align: super; }}

  .cs-note {{
    font-size: 12px; color: {FG_MUTED};
    border-left: 2px solid {DUO_DIM}; padding: 8px 0 8px 12px; margin: 10px 0;
  }}

  /* 위젯을 두 톤에 맞춘다 */
  .stButton > button {{
    background: {DUO_500}; color: #FFFFFF; border: none; border-radius: 2px;
    font-weight: 800; letter-spacing: 0.02em;
  }}
  .stButton > button:hover {{ background: {DUO_400}; color: #FFFFFF; }}
  .stDownloadButton > button {{
    background: {SURFACE}; color: {DUO_500};
    border: 1px solid {DUO_500}; border-radius: 2px; font-weight: 700;
  }}
  .stDownloadButton > button:hover {{ background: {DUO_FAINT}; }}
  [data-testid="stSidebar"] {{
    background: {SURFACE}; border-right: 1px solid {INK_600};
  }}
  .stTabs [data-baseweb="tab-list"] {{ gap: 2px; border-bottom: 1px solid {INK_600}; }}
  .stTabs [data-baseweb="tab"] {{
    background: transparent; border-radius: 0; color: {FG_MUTED};
    font-weight: 700; letter-spacing: 0.04em;
  }}
  .stTabs [aria-selected="true"] {{
    color: {DUO_500} !important; border-bottom: 2px solid {DUO_500};
  }}
  .stTabs [aria-selected="true"] p {{ color: {DUO_500} !important; }}
  .stProgress > div > div > div > div {{ background: {DUO_500}; }}
</style>
"""


def chip_html(category: str) -> str:
    """카테고리 칩 HTML."""
    kind, _ = CATEGORY_STYLE.get(category, ("dim", FG_MUTED))
    cls = "" if kind == "outline" else kind
    return f'<span class="cs-chip {cls}">{category}</span>'

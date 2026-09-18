# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A tool that collects domestic/international news, press releases, reports, and papers about **steel industry
carbon neutrality** for a given date range and extra topics, scores them for relevance, picks representative
articles, and generates LLM summaries/implications plus a one-page Summary. Ships both a Streamlit web UI
(`app.py`) and a CLI (`python -m carbon_sensing.main`) that share the exact same pipeline — never duplicate
business logic into the UI layer; UI code only calls into `carbon_sensing.pipeline` / `carbon_sensing.rerun`.

## Commands

```bash
# Install
python -m pip install -r requirements.txt
python -m playwright install chromium   # required once, for PDF generation

# Run the web UI
streamlit run app.py

# Run the CLI
python -m carbon_sensing.main run --from 2026-09-01 --to 2026-09-15
python -m carbon_sensing.main run --preset 30d --topic "CBAM" --top-k 7
python -m carbon_sensing.main run --preset 30d --no-llm         # skip LLM/embedding stages
python -m carbon_sensing.main rerun outputs/<run-folder>         # regenerate report without re-collecting
python -m carbon_sensing.main rerun outputs/<run-folder> --pick <doc_id> --pick <doc_id>
python -m carbon_sensing.main check                              # verify .env/config, no network calls
python -m carbon_sensing.main presets

# Tests
python -m pytest tests -q
python -m pytest tests/test_scoring.py -q                        # single file
python -m pytest tests/test_scoring.py::test_off_topic_still_zero_with_semantic -q  # single test
```

There is no lint/build/type-check command configured in this repo (no pyproject.toml/ruff/mypy config) —
don't invent one.

Note: this is a Windows environment (PowerShell/Git Bash). Console output must go through
`ensure_utf8_stdout()` (in `config.py`) or Korean text mangles; it's already called at the top of `main.py`
and `app.py`.

## Architecture

### Pipeline is the single source of truth

`carbon_sensing/pipeline.py::run_pipeline()` is the one place the full flow is wired: build queries → collect
→ dedupe → filter by period → extract bodies → categorize → embed (semantic score) → score → select
representative → summarize → synthesize. Both `main.py` (CLI) and `ui/runner.py` (Streamlit) call
`run_and_write()` from this module — they never re-implement any of these steps. When adding a pipeline stage,
add it here once.

`rerun.py::rerun_llm()` is a separate, narrower path: it restores a `RunResult` from a previous run's
`raw.json` (via `load_run()`) and only redoes representative-selection → summarize → synthesize → re-render
outputs. It does **not** re-collect or re-score. Bodies are restored from the SQLite cache
(`storage/db.py::get_body`), not re-fetched.

### Everything degrades gracefully when a key is missing

Every collector (`collectors/*.py`) checks `available()` before running and returns a `CollectorResult` with
`skipped_reason` set rather than raising — the pipeline always continues with whatever sources it has.
Likewise `llm.build_client()` returns `None` (not an exception) when `OPENAI_API_KEY` is absent, and
`score_documents()` accepts `semantic_scores=None` to fall back to a 4-metric (no embedding) score with
weights renormalized — see `scoring/keyword.py`'s module docstring for the exact renormalization rule. Never
make a new feature hard-require a key; follow this same "degrade, don't crash" pattern.

### Scoring: gate vs. weighted score are different mechanisms

`scoring/keyword.py::topic_gate()` decides on/off-topic (steel keyword AND carbon keyword both required, else
score = 0) — this is separate from the five weighted metrics (semantic/keyword/source_trust/recency/spread,
40/20/20/10/10 from the PRD) that produce the 0–100 relevance score. Don't conflate them: a document can have
a high weighted score but still be zeroed by the gate.

Keyword matching (`scoring/matching.py`) has two modes and picking the wrong one reintroduces real bugs found
during development:
- `strict=True` (default): word-boundary aware, used by the topic gate and keyword-fit scoring. Prevents false
  positives like `고로` matching inside `참고로`, or `제철` matching the unrelated `제철 과일` (seasonal fruit).
- `strict=False`: plain substring match, used only by `processing/categorize.py`. Needed because legitimate
  compounds like `실증설비` contain `설비` without a word boundary — strict mode would miss them.

`topics.yaml`'s `carbon_keywords` deliberately avoids bare English words like `carbon`, `hydrogen`, `emission`
even though `strict=True` word-boundary matching would let them through cleanly — the problem isn't false word
boundaries, it's false *domain*: `carbon` matches "carbon **steel**" (an alloy classification, not emissions),
`hydrogen` matches "hydrogen **embrittlement**" (materials degradation), `emission` matches "**emission**
spectroscopy" (an analytical technique). These wrongly passed the topic gate for a large share of OpenAlex
results before being caught (verified against a real run: 32 of 92 documents above the display threshold were
this kind of noise). Use specific phrases (`carbon emission`, `green hydrogen`, `hydrogen reduction`, etc.)
instead of bare nouns, and add title-only `default_excludes` entries (`corrosion inhibit`, `embrittlement`) for
recurring false-positive paper genres rather than trying to enumerate every safe compound phrase.

### Category classification is separate from relevance scoring

`processing/categorize.py` assigns one of 설비/정책/기술/시장/기타 per document by counting non-strict keyword
hits from `topics.yaml`'s `category_keywords`, no LLM call. This runs after dedup/body-extraction and before
scoring in the pipeline. Category drives grouping in every output (report tables, representative-article
sections, Summary trends) and the chip styling in `report/html_renderer.py::CATEGORY_CHIP` /
`ui/theme.py::CATEGORY_STYLE` — keep the 5-way distinction using shape (fill/outline/dim/dash), not new colors,
if you touch either.

### LLM layer: structured output + grounding + caching

`llm/client.py::LlmClient.structured()` is the only way to call the model — it always parses into a pydantic
schema (`llm/schemas.py`), caches responses in SQLite keyed by (model, system, user, schema) so identical reruns
cost zero tokens, and enforces `MAX_TOKENS_PER_RUN` *before* calling (raises `TokenBudgetExceeded`, which both
CLI and UI catch and abort cleanly — see `main.py`'s `run`/`rerun` commands).

`llm/service.py::_ground()` is a correctness-critical function: every LLM-produced claim carries a
`source_doc_id` that must match a real document id from the ids given in the prompt. If it doesn't (hallucinated
id, or literal `"unknown"`), `_ground()` does **not** silently drop the sentence — it appends "(원문 미확인)" and
marks `verified=False` so the fabrication stays visible in reports rather than disappearing. Any new LLM call
point must route through `_ground()` the same way.

There are exactly four LLM call sites (`llm/service.py`): `expand_queries` (light model), `select_representative`,
`summarize_document(s)`, `synthesize`. Prompts live in `llm/prompts.py` — the system prompt's rules (no facts
beyond the given body text, verbatim numbers, Korean output, per-sentence source ids) apply to all four.

### Semantic scoring cache

`scoring/embedding.py::semantic_scores()` embeds a fixed topic-description string once and each document's
title+body once, caching vectors in SQLite by text hash (`storage/db.py`'s `embeddings` table) — reruns over
the same documents make zero embedding calls. Documents missing a body get a `no_body_penalty` (config.yaml)
applied to their semantic score only, not to the whole score (that would double-penalize when `keyword_component`
already scores lower for missing body text).

### Date handling: everything normalizes through KST

`daterange.py::DateRange` always stores UTC-aware datetimes internally, but `build_range()` treats naive
datetimes and date-only inputs as KST (Asia/Seoul) before converting — most Korean news sources have no explicit
timezone and are implicitly KST. `contains()` is the only correct way to test whether a document's
`published_at` falls in range; don't compare datetimes directly.

### Report generation: three renderers, one `RunResult`

`report/markdown_renderer.py::RunResult` is the shared data object every renderer consumes
(`render_report` → `report.md`, `report/summary_renderer.py::render_summary_one_page` → `summary.md`,
`report/html_renderer.py` → `report.html`/`summary.html` → PDF via Playwright). `write_outputs()` is the single
place all outputs get written; add a new output format there, not ad hoc in the pipeline.

The one-page Summary PDF is *measured*, not hard-scaled: `html_renderer.py::_render_pdf_sync` renders once,
reads `document.documentElement.scrollHeight` via `page.evaluate`, then computes a print `scale` so it fits one
A4 page. A fixed scale constant was tried first and produced 2-page PDFs depending on how many representative
articles/categories were present — don't revert to a fixed scale.

Playwright's sync API cannot run inside an asyncio event loop (the pipeline is async). `html_to_pdf()` always
dispatches through a `ThreadPoolExecutor` for this reason — don't call `_render_pdf_sync` directly from async
code.

On Streamlit Community Cloud (or any Linux container), the Chromium binary and Korean fonts aren't
preinstalled: `packages.txt` lists the apt libraries Chromium needs plus `fonts-noto-cjk` (without it, Hangul
renders as tofu boxes — `base.css`'s `--font-sans` is Windows/Mac-first and Linux has none of those), and
`html_renderer.py::_launch_chromium()` lazily runs `playwright install chromium` on first launch failure since
the platform doesn't run that step automatically.

CSS lives in one file (`report/templates/base.css`) shared by both HTML templates, with a `@media print` block
that inverts the dark duotone (ink navy + blue) to a light, toner-light variant. The Streamlit theme
(`ui/theme.py`) duplicates the same two-tone palette and hero/band/chip styles by design so the web UI and the
generated reports look like one system — keep them in sync if you change the palette.

Known CSS trap: `.hero` needs an explicit `width: 100%` before `aspect-ratio: 16/9` — aspect-ratio alone with a
`max-height` constraint shrinks the box's *width* instead of capping height, which silently produces a narrow
banner (caught via a rendered screenshot, not a test).

### Configuration split

- `.env` — secrets and per-machine settings only (`config.py::Settings`, pydantic-settings).
- `config.yaml` — tunable pipeline behavior (concurrency, retry counts, scoring weights/thresholds, report
  sizes).
- `topics.yaml` — the fixed base topic, its query expansions, keyword lists (steel/carbon/bonus/category),
  default excludes, and saved topic profiles (`profiles:` — read/write via `ui/runner.py::save_profile`).
- `sources.yaml` — domain → trust tier mapping and the RSS feed list.

All four are loaded through `@lru_cache`'d functions in `config.py`; call `reset_config_cache()` after writing
to any of the YAML files at runtime (the UI's profile save does this) or stale data will be served.

On Streamlit Community Cloud there is no `.env` file (it's gitignored, so it never reaches the deployed repo);
secrets are entered in the dashboard's Secrets panel instead and only exposed via `st.secrets`, not as OS
environment variables. `app.py` bridges this at import time — before `carbon_sensing.config` loads — by copying
`st.secrets` into `os.environ` so `Settings()` (which only reads env vars/`.env`) picks them up unchanged. Keep
Secrets-panel key names identical to the `.env` names for this to work.

### Storage

`storage/db.py::Database` is one SQLite file (`outputs/carbon_sensing.db`, WAL mode) shared by CLI and UI
across runs, holding: extracted article bodies, document history, LLM response cache, and embedding cache. This
is what makes `rerun` and repeated UI runs cheap — don't bypass it with ad hoc file caches.

"""SQLite 저장소. 본문 캐시와 문서 이력을 담아 재호출을 피한다."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import PROJECT_DIR
from ..models import Document

DEFAULT_DB_PATH = PROJECT_DIR / "outputs" / "carbon_sensing.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bodies (
    url_hash   TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    body       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    url_hash     TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    title        TEXT NOT NULL,
    source_name  TEXT,
    published_at TEXT,
    region       TEXT,
    doc_type     TEXT,
    collector    TEXT,
    payload      TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key   TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS embeddings (
    text_hash  TEXT NOT NULL,
    model      TEXT NOT NULL,
    vector     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (text_hash, model)
);
CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    context    TEXT NOT NULL
);
"""


def url_hash(url: str) -> str:
    from ..processing.dedupe import normalize_url

    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()


class Database:
    """단순 래퍼. UI와 CLI가 동시에 붙어도 되도록 WAL을 켠다."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            self.path, timeout=30.0, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --- 본문 캐시 ---

    def get_body(self, url: str) -> str | None:
        row = self.conn.execute(
            "SELECT body FROM bodies WHERE url_hash = ?", (url_hash(url),)
        ).fetchone()
        return row["body"] if row else None

    def save_body(self, url: str, body: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO bodies (url_hash, url, body, fetched_at)"
            " VALUES (?, ?, ?, ?)",
            (url_hash(url), url, body, _now()),
        )
        self.conn.commit()

    # --- 문서 이력 ---

    def save_documents(self, documents: list[Document]) -> None:
        rows = [
            (
                url_hash(d.url),
                d.url,
                d.title,
                d.source_name,
                d.published_at.isoformat() if d.published_at else None,
                d.region,
                d.doc_type,
                d.collector,
                json.dumps(d.to_dict(), ensure_ascii=False),
                _now(),
            )
            for d in documents
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO documents"
            " (url_hash, url, title, source_name, published_at, region, doc_type,"
            "  collector, payload, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()

    def save_run(self, output_dir: str, context: dict) -> None:
        self.conn.execute(
            "INSERT INTO runs (started_at, output_dir, context) VALUES (?, ?, ?)",
            (_now(), output_dir, json.dumps(context, ensure_ascii=False)),
        )
        self.conn.commit()

    # --- LLM 응답 캐시 ---

    def get_llm_response(self, cache_key: str) -> str | None:
        row = self.conn.execute(
            "SELECT response FROM llm_cache WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        return row["response"] if row else None

    def save_llm_response(
        self, cache_key: str, model: str, schema_name: str, response: str
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_cache"
            " (cache_key, model, schema_name, response, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (cache_key, model, schema_name, response, _now()),
        )
        self.conn.commit()

    # --- 임베딩 캐시 ---

    def get_embedding(self, text_hash: str, model: str) -> list[float] | None:
        row = self.conn.execute(
            "SELECT vector FROM embeddings WHERE text_hash = ? AND model = ?",
            (text_hash, model),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["vector"])
        except (json.JSONDecodeError, TypeError):
            return None

    def save_embeddings(
        self, model: str, items: list[tuple[str, list[float]]]
    ) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO embeddings"
            " (text_hash, model, vector, created_at) VALUES (?, ?, ?, ?)",
            [
                (text_hash, model, json.dumps(vector), _now())
                for text_hash, vector in items
            ],
        )
        self.conn.commit()

    def count_bodies(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS c FROM bodies").fetchone()["c"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

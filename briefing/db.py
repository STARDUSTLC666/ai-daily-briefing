from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Document, EvidenceCard, EvidenceExcerpt, FeedItem, Source, SourceHealth, iso

UTC = timezone.utc

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tier TEXT NOT NULL,
  type TEXT NOT NULL,
  region TEXT NOT NULL,
  url TEXT NOT NULL,
  enabled INTEGER NOT NULL,
  reliability TEXT NOT NULL,
  topics_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_health (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  status TEXT NOT NULL,
  status_code INTEGER,
  item_count INTEGER NOT NULL,
  latest_item_at TEXT,
  latency_ms INTEGER,
  error TEXT NOT NULL,
  final_url TEXT NOT NULL,
  stale INTEGER NOT NULL,
  no_date_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  guid TEXT NOT NULL,
  title TEXT NOT NULL,
  link TEXT NOT NULL,
  summary TEXT NOT NULL,
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  unique_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_items_published_at ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id);
CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  item_unique_key TEXT NOT NULL,
  source_id TEXT NOT NULL,
  url TEXT NOT NULL,
  final_url TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  published_at TEXT,
  language TEXT NOT NULL,
  quality_score INTEGER NOT NULL,
  metadata_json TEXT NOT NULL,
  UNIQUE(item_unique_key, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_documents_item ON documents(item_unique_key);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
CREATE TABLE IF NOT EXISTS evidence_excerpts (
  excerpt_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  text TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  start_offset INTEGER,
  end_offset INTEGER,
  extractor TEXT NOT NULL,
  score INTEGER NOT NULL,
  metadata_json TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(document_id) ON DELETE CASCADE,
  UNIQUE(document_id, text_hash)
);
CREATE INDEX IF NOT EXISTS idx_excerpts_document ON evidence_excerpts(document_id);
CREATE TABLE IF NOT EXISTS evidence_cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT NOT NULL,
  cluster_key TEXT NOT NULL,
  event_title TEXT NOT NULL,
  entity TEXT NOT NULL,
  risk TEXT NOT NULL,
  confidence INTEGER NOT NULL,
  selected INTEGER NOT NULL,
  reason TEXT NOT NULL,
  source_count INTEGER NOT NULL,
  official_count INTEGER NOT NULL,
  media_count INTEGER NOT NULL,
  community_count INTEGER NOT NULL,
  first_seen_at TEXT NOT NULL,
  latest_published_at TEXT,
  key_facts_json TEXT NOT NULL,
  evidence_links_json TEXT NOT NULL,
  uncertainty_json TEXT NOT NULL,
  score REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_cards_run_date ON evidence_cards(run_date, selected);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def upsert_sources(conn: sqlite3.Connection, sources: list[Source]) -> None:
    now = datetime.now(UTC).isoformat()
    with conn:
        for s in sources:
            conn.execute(
                """
                INSERT INTO sources(id,name,tier,type,region,url,enabled,reliability,topics_json,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,tier=excluded.tier,type=excluded.type,region=excluded.region,
                  url=excluded.url,enabled=excluded.enabled,reliability=excluded.reliability,
                  topics_json=excluded.topics_json,updated_at=excluded.updated_at
                """,
                (
                    s.id,
                    s.name,
                    s.tier,
                    s.type,
                    s.region,
                    s.url,
                    int(s.enabled),
                    s.reliability,
                    json.dumps(s.topics, ensure_ascii=False),
                    now,
                ),
            )


def insert_health(conn: sqlite3.Connection, h: SourceHealth) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO source_health(source_id,checked_at,status,status_code,item_count,latest_item_at,latency_ms,error,final_url,stale,no_date_count)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                h.source_id,
                datetime.now(UTC).isoformat(),
                h.status,
                h.status_code,
                h.item_count,
                iso(h.latest_item_at) or None,
                h.latency_ms,
                h.error,
                h.final_url,
                int(h.stale),
                h.no_date_count,
            ),
        )


def insert_items(conn: sqlite3.Connection, items: list[FeedItem]) -> int:
    inserted = 0
    with conn:
        for it in items:
            unique_key = f"{it.source_id}:{it.guid or it.link or it.title}"
            exists = conn.execute("SELECT 1 FROM items WHERE unique_key = ?", (unique_key,)).fetchone() is not None
            try:
                conn.execute(
                    """
                    INSERT INTO items(source_id,guid,title,link,summary,published_at,fetched_at,raw_json,unique_key)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(unique_key) DO UPDATE SET
                      title=excluded.title,
                      link=excluded.link,
                      summary=excluded.summary,
                      published_at=excluded.published_at,
                      fetched_at=excluded.fetched_at,
                      raw_json=excluded.raw_json
                    """,
                    (
                        it.source_id,
                        it.guid,
                        it.title,
                        it.link,
                        it.summary,
                        iso(it.published_at) or None,
                        iso(it.fetched_at),
                        json.dumps(it.raw, ensure_ascii=False),
                        unique_key,
                    ),
                )
                if not exists:
                    inserted += 1
            except sqlite3.IntegrityError:
                pass
    return inserted


def save_document(conn: sqlite3.Connection, document: Document, excerpts: list[EvidenceExcerpt] | None = None) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO documents(
              document_id,item_unique_key,source_id,url,final_url,title,description,content,content_hash,
              extractor,extractor_version,fetched_at,published_at,language,quality_score,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(document_id) DO UPDATE SET
              final_url=excluded.final_url,title=excluded.title,description=excluded.description,
              content=excluded.content,content_hash=excluded.content_hash,extractor=excluded.extractor,
              extractor_version=excluded.extractor_version,fetched_at=excluded.fetched_at,
              published_at=excluded.published_at,language=excluded.language,
              quality_score=excluded.quality_score,metadata_json=excluded.metadata_json
            """,
            (
                document.document_id, document.item_unique_key, document.source_id, document.url, document.final_url,
                document.title, document.description, document.content, document.content_hash, document.extractor,
                document.extractor_version, iso(document.fetched_at), iso(document.published_at) or None,
                document.language, document.quality_score, json.dumps(document.metadata, ensure_ascii=False),
            ),
        )
        for excerpt in excerpts or []:
            conn.execute(
                """
                INSERT INTO evidence_excerpts(
                  excerpt_id,document_id,text,text_hash,start_offset,end_offset,extractor,score,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(excerpt_id) DO UPDATE SET
                  text=excluded.text,text_hash=excluded.text_hash,start_offset=excluded.start_offset,
                  end_offset=excluded.end_offset,extractor=excluded.extractor,score=excluded.score,
                  metadata_json=excluded.metadata_json
                """,
                (
                    excerpt.excerpt_id, excerpt.document_id, excerpt.text, excerpt.text_hash,
                    excerpt.start_offset, excerpt.end_offset, excerpt.extractor, excerpt.score,
                    json.dumps(excerpt.metadata, ensure_ascii=False),
                ),
            )


def load_latest_document(conn: sqlite3.Connection, item_unique_key: str) -> tuple[Document, list[EvidenceExcerpt]] | None:
    row = conn.execute(
        "SELECT * FROM documents WHERE item_unique_key=? ORDER BY fetched_at DESC LIMIT 1", (item_unique_key,)
    ).fetchone()
    if row is None:
        return None
    document = Document(
        document_id=row["document_id"], item_unique_key=row["item_unique_key"], source_id=row["source_id"],
        url=row["url"], final_url=row["final_url"], title=row["title"], description=row["description"],
        content=row["content"], content_hash=row["content_hash"], extractor=row["extractor"],
        extractor_version=row["extractor_version"], fetched_at=_parse_dt(row["fetched_at"]) or datetime.now(UTC),
        published_at=_parse_dt(row["published_at"]), language=row["language"], quality_score=row["quality_score"],
        metadata=json.loads(row["metadata_json"] or "{}"),
    )
    excerpt_rows = conn.execute(
        "SELECT * FROM evidence_excerpts WHERE document_id=? ORDER BY score DESC, rowid ASC", (document.document_id,)
    ).fetchall()
    excerpts = [
        EvidenceExcerpt(
            excerpt_id=ex["excerpt_id"], document_id=ex["document_id"], text=ex["text"], text_hash=ex["text_hash"],
            start_offset=ex["start_offset"], end_offset=ex["end_offset"], extractor=ex["extractor"], score=ex["score"],
            metadata=json.loads(ex["metadata_json"] or "{}"),
        )
        for ex in excerpt_rows
    ]
    return document, excerpts


def load_recent_items(conn: sqlite3.Connection, since: datetime, limit: int = 1000) -> list[FeedItem]:
    rows = conn.execute(
        """
        SELECT i.*, s.name AS source_name, s.tier AS source_tier, s.reliability AS source_reliability
        FROM items i JOIN sources s ON i.source_id = s.id
        WHERE (i.published_at IS NOT NULL AND i.published_at >= ?)
           OR (i.published_at IS NULL AND i.fetched_at >= ?)
        ORDER BY COALESCE(i.published_at, i.fetched_at) DESC
        LIMIT ?
        """,
        (since.isoformat(), since.isoformat(), limit),
    ).fetchall()
    result: list[FeedItem] = []
    for r in rows:
        raw = json.loads(r["raw_json"] or "{}")
        result.append(
            FeedItem(
                source_id=r["source_id"],
                source_name=r["source_name"],
                source_tier=r["source_tier"],
                source_reliability=r["source_reliability"],
                title=r["title"],
                link=r["link"],
                guid=r["guid"],
                summary=r["summary"],
                published_at=_parse_dt(r["published_at"]),
                fetched_at=_parse_dt(r["fetched_at"]) or datetime.now(UTC),
                raw=raw,
            )
        )
    return result


def load_story_history(conn: sqlite3.Connection, *, before_run_date: str, lookback_runs: int = 14) -> dict[str, int]:
    """Count recently selected story keys before the current run date.

    The cluster key is deliberately used as the stable persisted identity. The
    selector treats these counts as a soft penalty, never as a publication ban.
    """
    rows = conn.execute(
        """
        SELECT cluster_key, COUNT(DISTINCT run_date) AS selected_runs
        FROM evidence_cards
        WHERE selected = 1 AND run_date < ?
          AND run_date IN (
            SELECT DISTINCT run_date FROM evidence_cards
            WHERE run_date < ? ORDER BY run_date DESC LIMIT ?
          )
        GROUP BY cluster_key
        """,
        (before_run_date, before_run_date, max(1, int(lookback_runs))),
    ).fetchall()
    return {str(row["cluster_key"]): int(row["selected_runs"] or 0) for row in rows}


def save_evidence_cards(conn: sqlite3.Connection, run_date: str, cards: list[EvidenceCard]) -> None:
    with conn:
        conn.execute("DELETE FROM evidence_cards WHERE run_date = ?", (run_date,))
        for c in cards:
            conn.execute(
                """
                INSERT INTO evidence_cards(
                    run_date,cluster_key,event_title,entity,risk,confidence,selected,reason,
                    source_count,official_count,media_count,community_count,first_seen_at,latest_published_at,
                    key_facts_json,evidence_links_json,uncertainty_json,score
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_date,
                    c.cluster_key,
                    c.event_title,
                    c.entity,
                    c.risk,
                    c.confidence,
                    int(c.selected),
                    c.reason,
                    c.source_count,
                    c.official_count,
                    c.media_count,
                    c.community_count,
                    iso(c.first_seen_at),
                    iso(c.latest_published_at) or None,
                    json.dumps(c.key_facts, ensure_ascii=False),
                    json.dumps(c.evidence_links, ensure_ascii=False),
                    json.dumps(c.uncertainty, ensure_ascii=False),
                    c.score,
                ),
            )

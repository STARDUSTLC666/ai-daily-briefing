from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import EvidenceCard, FeedItem, Source, SourceHealth, iso

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
        for src in sources:
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
                    src.id,
                    src.name,
                    src.tier,
                    src.type,
                    src.region,
                    src.url,
                    int(src.enabled),
                    src.reliability,
                    json.dumps(src.topics, ensure_ascii=False),
                    now,
                ),
            )


def insert_health(conn: sqlite3.Connection, health: SourceHealth) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO source_health(source_id,checked_at,status,status_code,item_count,latest_item_at,latency_ms,error,final_url,stale,no_date_count)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                health.source_id,
                datetime.now(UTC).isoformat(),
                health.status,
                health.status_code,
                health.item_count,
                iso(health.latest_item_at) or None,
                health.latency_ms,
                health.error,
                health.final_url,
                int(health.stale),
                health.no_date_count,
            ),
        )


def insert_items(conn: sqlite3.Connection, items: list[FeedItem]) -> int:
    inserted = 0
    with conn:
        for item in items:
            unique_key = f"{item.source_id}:{item.guid or item.link or item.title}"
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
                        item.source_id,
                        item.guid,
                        item.title,
                        item.link,
                        item.summary,
                        iso(item.published_at) or None,
                        iso(item.fetched_at),
                        json.dumps(item.raw, ensure_ascii=False),
                        unique_key,
                    ),
                )
                if not exists:
                    inserted += 1
            except sqlite3.IntegrityError:
                pass
    return inserted


def load_recent_items(conn: sqlite3.Connection, since: datetime, limit: int = 1000) -> list[FeedItem]:
    cutoff = iso(since)
    rows = conn.execute(
        """
        SELECT i.*, s.name AS source_name, s.tier AS source_tier, s.reliability AS source_reliability
        FROM items i JOIN sources s ON i.source_id = s.id
        WHERE (i.published_at IS NOT NULL AND i.published_at >= ?)
           OR (i.published_at IS NULL AND i.fetched_at >= ?)
        ORDER BY COALESCE(i.published_at, i.fetched_at) DESC
        LIMIT ?
        """,
        (cutoff, cutoff, limit),
    ).fetchall()
    return [
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
            raw=json.loads(r["raw_json"] or "{}"),
        )
        for r in rows
    ]


def load_story_history(conn: sqlite3.Connection, *, before_run_date: str, lookback_runs: int = 14) -> dict[str, int]:
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
        for card in cards:
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
                    card.cluster_key,
                    card.event_title,
                    card.entity,
                    card.risk,
                    card.confidence,
                    int(card.selected),
                    card.reason,
                    card.source_count,
                    card.official_count,
                    card.media_count,
                    card.community_count,
                    iso(card.first_seen_at),
                    iso(card.latest_published_at) or None,
                    json.dumps(card.key_facts, ensure_ascii=False),
                    json.dumps(card.evidence_links, ensure_ascii=False),
                    json.dumps(card.uncertainty, ensure_ascii=False),
                    card.score,
                ),
            )

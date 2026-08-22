"""SQLite schema and connection handling. No domain rules live here."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  domain TEXT NOT NULL,
  tier INTEGER,
  independence_group TEXT,
  title TEXT,
  published_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY,
  occurred_at TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  district TEXT NOT NULL,
  claimed_district TEXT,
  address_text TEXT,
  structure_type TEXT NOT NULL,
  killed INTEGER,
  injured INTEGER,
  damage TEXT NOT NULL,
  verification_level TEXT NOT NULL CHECK (verification_level IN
      ('unverified','single_source','corroborated','well_documented')),
  geocode_confidence REAL NOT NULL,
  needs_review INTEGER NOT NULL DEFAULT 0,
  review_reason TEXT,
  reviewed_by TEXT,
  reviewed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_time ON incidents(occurred_at);
CREATE INDEX IF NOT EXISTS idx_incidents_bbox ON incidents(lat, lon);
CREATE INDEX IF NOT EXISTS idx_incidents_review ON incidents(needs_review);

CREATE TABLE IF NOT EXISTS incident_claims (
  id INTEGER PRIMARY KEY,
  incident_id INTEGER NOT NULL REFERENCES incidents(id),
  source_id   INTEGER NOT NULL REFERENCES sources(id),
  field TEXT NOT NULL,
  value TEXT NOT NULL,
  quote TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN
      ('corroborated','single_source','conflicting','superseded')),
  extracted_at TEXT NOT NULL,
  UNIQUE(incident_id, source_id, field)
);

CREATE TABLE IF NOT EXISTS incident_media (
  id INTEGER PRIMARY KEY,
  incident_id INTEGER NOT NULL REFERENCES incidents(id),
  source_id   INTEGER NOT NULL REFERENCES sources(id),
  url TEXT NOT NULL,
  caption TEXT,
  UNIQUE(incident_id, url)
);

CREATE TABLE IF NOT EXISTS geocode_cache (
  query_key TEXT PRIMARY KEY,
  response_json TEXT NOT NULL,
  cached_at TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the incident database, creating the schema if needed."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn

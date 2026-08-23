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

-- One night of shelling. City-wide figures live here and nowhere else: put
-- "5 killed in Kyiv" on each of three impact rows and any sum over districts,
-- or over a month, returns three times the real number.
CREATE TABLE IF NOT EXISTS attacks (
  id INTEGER PRIMARY KEY,
  occurred_at TEXT NOT NULL,
  citywide_killed INTEGER,
  citywide_injured INTEGER,
  weapons TEXT NOT NULL DEFAULT '[]',
  verification_level TEXT NOT NULL CHECK (verification_level IN
      ('unverified','single_source','corroborated','well_documented')),
  needs_review INTEGER NOT NULL DEFAULT 0,
  review_reason TEXT,
  reviewed_by TEXT,
  reviewed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attacks_time ON attacks(occurred_at);
CREATE INDEX IF NOT EXISTS idx_attacks_review ON attacks(needs_review);

CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY,
  attack_id INTEGER REFERENCES attacks(id),
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

-- Same shape as incident_claims, for the attack level.
CREATE TABLE IF NOT EXISTS attack_claims (
  id INTEGER PRIMARY KEY,
  attack_id INTEGER NOT NULL REFERENCES attacks(id),
  source_id INTEGER NOT NULL REFERENCES sources(id),
  field TEXT NOT NULL,
  value TEXT NOT NULL,
  quote TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN
      ('corroborated','single_source','conflicting','superseded')),
  extracted_at TEXT NOT NULL,
  UNIQUE(attack_id, source_id, field)
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


# Columns added after the first version of the schema. CREATE TABLE IF NOT
# EXISTS will not add them to a database that already exists, so a file left
# over from an earlier run would fail on the first insert with a confusing
# message. Check for them and say what to do instead.
REQUIRED_INCIDENT_COLUMNS = ("attack_id", "claimed_district", "needs_review",
                             "review_reason")


def _check_schema(conn: sqlite3.Connection, path: str | Path) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(incidents)")}
    if not columns:
        return                      # brand new database, nothing to check
    missing = [c for c in REQUIRED_INCIDENT_COLUMNS if c not in columns]
    if missing:
        raise RuntimeError(
            f"The database at {path} was created by an older version of this "
            f"project and is missing: {', '.join(missing)}.\n"
            "Nothing in it is irreplaceable — every record can be rebuilt by "
            "running the agent again. Delete the file and start over."
        )


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the incident database, creating the schema if needed."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _check_schema(conn, path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn

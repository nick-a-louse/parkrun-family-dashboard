"""SQLite storage. One file, created on first start, safe to back up while running."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runners (
    athlete_id     INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,        -- name from config
    parkrun_name   TEXT,                 -- name as shown on parkrun
    total_5k       INTEGER,
    total_junior   INTEGER,
    gender         TEXT,
    age_group      TEXT,
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS results (
    athlete_id      INTEGER NOT NULL,
    event_slug      TEXT NOT NULL,
    run_number      INTEGER NOT NULL,
    event_name      TEXT NOT NULL,
    distance        TEXT NOT NULL,       -- '2k' or '5k'
    run_date        TEXT NOT NULL,       -- ISO date
    position        INTEGER,
    time_seconds    INTEGER NOT NULL,
    age_grade       REAL,
    parkrun_pb      INTEGER NOT NULL DEFAULT 0,
    gender          TEXT,
    gender_position INTEGER,
    gender_count    INTEGER,
    finishers       INTEGER,
    age_group       TEXT,
    event_checked_at TEXT,               -- when the event results page was last read
    first_seen_at   TEXT NOT NULL,
    notified        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (athlete_id, event_slug, run_number)
);
CREATE INDEX IF NOT EXISTS results_date ON results (run_date);

CREATE TABLE IF NOT EXISTS fetch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    trigger     TEXT NOT NULL,           -- schedule / manual / catchup / import / startup
    ok          INTEGER,
    new_results INTEGER DEFAULT 0,
    requests    INTEGER DEFAULT 0,
    message     TEXT
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self):
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params)

    # --- state key/value -------------------------------------------------
    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM state WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_state(self, key: str, value) -> None:
        self.execute("INSERT INTO state(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))

    def close(self):
        with self._lock:
            self._conn.close()

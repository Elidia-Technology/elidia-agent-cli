import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from elidia.config.settings import ELIDIA_HOME

logger = logging.getLogger(__name__)

DB_PATH = ELIDIA_HOME / "elidia.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Session',
    model TEXT,
    mode TEXT DEFAULT 'chat',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    model TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cost_dt REAL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    action TEXT NOT NULL,
    detail TEXT,
    session_id TEXT
);
"""


class Database:
    def __init__(self, path: Path | None = None):
        logger.debug(f"Entered into Database.__init__: path={path}")
        self._path = path or DB_PATH
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        logger.debug(f"Entered into Database.connect: path={self._path}")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        logger.debug("Entered into Database.close: closing connection")
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def cursor(self):
        logger.debug("Entered into Database.cursor: opening cursor")
        if not self._conn:
            self.connect()
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def execute(self, sql: str, params=()) -> list[sqlite3.Row]:
        logger.debug(f"Entered into Database.execute: sql={sql}")
        if not self._conn:
            self.connect()
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur.fetchall()

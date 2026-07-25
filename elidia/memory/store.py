import json
import logging
import sqlite3
import struct
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

import sqlite_vec

from elidia.config.settings import ELIDIA_HOME

logger = logging.getLogger(__name__)

MEMORY_DB_PATH = ELIDIA_HOME / "memory.db"
EMBEDDING_DIM = 1024


class MemoryTier(IntEnum):
    SYSTEM = 1
    USER = 2
    PROJECT = 3
    SESSION = 4


@dataclass
class MemoryEntry:
    id: str = ""
    tier: MemoryTier = MemoryTier.SESSION
    key: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    project_path: str = ""
    session_id: str = ""
    source: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    access_count: int = 0
    embedding: list[float] | None = None


class MemoryStore:
    """Persistent memory store backed by SQLite + sqlite-vec for vector search."""

    def __init__(self, db_path: Path | None = None, embedding_dim: int = EMBEDDING_DIM) -> None:
        logger.debug(f"Entered into MemoryStore.__init__: db_path={db_path}")
        self._db_path = db_path or MEMORY_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_dim = embedding_dim
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        logger.debug(f"Entered into MemoryStore.open: path={self._db_path}")
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._create_tables()

    def close(self) -> None:
        logger.debug("Entered into MemoryStore.close")
        if self._conn:
            self._conn.close()
            self._conn = None

    def _create_tables(self) -> None:
        logger.debug("Entered into _create_tables")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                tier INTEGER NOT NULL,
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                project_path TEXT DEFAULT '',
                session_id TEXT DEFAULT '',
                source TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                access_count INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
            CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(key);
            CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_path);
            CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);
            CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at DESC);
        """)

        try:
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vecs USING vec0(embedding float[{self._embedding_dim}])"
            )
        except sqlite3.OperationalError as e:
            if "already exists" not in str(e).lower():
                raise
        self._conn.commit()

    def save(self, entry: MemoryEntry) -> str:
        logger.debug(f"Entered into save: key={entry.key}, tier={entry.tier}")
        now = time.time()
        if not entry.id:
            entry.id = str(uuid.uuid4())
        if not entry.created_at:
            entry.created_at = now
        entry.updated_at = now

        self._conn.execute(
            """INSERT INTO memories (id, tier, key, content, metadata, project_path, session_id, source, created_at, updated_at, access_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 content=excluded.content,
                 metadata=excluded.metadata,
                 updated_at=excluded.updated_at,
                 access_count=excluded.access_count
            """,
            (
                entry.id, int(entry.tier), entry.key, entry.content,
                json.dumps(entry.metadata, default=str),
                entry.project_path, entry.session_id, entry.source,
                entry.created_at, entry.updated_at, entry.access_count,
            ),
        )

        if entry.embedding:
            blob = _floats_to_blob(entry.embedding)
            try:
                self._conn.execute(
                    "INSERT INTO memory_vecs(rowid, embedding) VALUES ((SELECT rowid FROM memories WHERE id=?), ?)",
                    (entry.id, blob),
                )
            except sqlite3.IntegrityError:
                self._conn.execute(
                    "UPDATE memory_vecs SET embedding=? WHERE rowid=(SELECT rowid FROM memories WHERE id=?)",
                    (blob, entry.id),
                )

        self._conn.commit()
        return entry.id

    def get(self, memory_id: str) -> MemoryEntry | None:
        logger.debug(f"Entered into get: id={memory_id}")
        row = self._conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if not row:
            return None
        entry = self._row_to_entry(row)
        self._conn.execute(
            "UPDATE memories SET access_count = access_count + 1 WHERE id=?", (memory_id,)
        )
        self._conn.commit()
        return entry

    def get_by_key(self, key: str, tier: MemoryTier | None = None) -> MemoryEntry | None:
        logger.debug(f"Entered into get_by_key: key={key}, tier={tier}")
        if tier is not None:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE key=? AND tier=? ORDER BY updated_at DESC LIMIT 1",
                (key, int(tier)),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE key=? ORDER BY updated_at DESC LIMIT 1", (key,)
            ).fetchone()
        return self._row_to_entry(row) if row else None

    def search_text(
        self,
        query: str,
        tier: MemoryTier | None = None,
        project_path: str = "",
        session_id: str = "",
        limit: int = 20,
    ) -> list[MemoryEntry]:
        logger.debug(f"Entered into search_text: query={query!r}, tier={tier}")
        conditions = ["(content LIKE ? OR key LIKE ?)"]
        params: list[Any] = [f"%{query}%", f"%{query}%"]

        if tier is not None:
            conditions.append("tier = ?")
            params.append(int(tier))
        if project_path:
            conditions.append("project_path = ?")
            params.append(project_path)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)

        where = " AND ".join(conditions)
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM memories WHERE {where} ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()

        return [self._row_to_entry(r) for r in rows]

    def search_vector(
        self,
        query_embedding: list[float],
        limit: int = 10,
        tier: MemoryTier | None = None,
        project_path: str = "",
    ) -> list[tuple[MemoryEntry, float]]:
        logger.debug(f"Entered into search_vector: dim={len(query_embedding)}, limit={limit}")
        blob = _floats_to_blob(query_embedding)

        rows = self._conn.execute(
            """
            SELECT m.*, v.distance
            FROM memory_vecs v
            JOIN memories m ON m.rowid = v.rowid
            WHERE v.embedding MATCH ?
            AND k = ?
            ORDER BY v.distance
            """,
            (blob, limit),
        ).fetchall()

        results: list[tuple[MemoryEntry, float]] = []
        for row in rows:
            distance = row[-1]
            entry = self._row_to_entry(row[:-1])
            if tier is not None and entry.tier != tier:
                continue
            if project_path and entry.project_path != project_path:
                continue
            results.append((entry, distance))

        return results[:limit]

    def list_memories(
        self,
        tier: MemoryTier | None = None,
        project_path: str = "",
        session_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryEntry]:
        logger.debug(f"Entered into list_memories: tier={tier}, limit={limit}")
        conditions: list[str] = []
        params: list[Any] = []

        if tier is not None:
            conditions.append("tier = ?")
            params.append(int(tier))
        if project_path:
            conditions.append("project_path = ?")
            params.append(project_path)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])

        rows = self._conn.execute(
            f"SELECT * FROM memories{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()

        return [self._row_to_entry(r) for r in rows]

    def delete(self, memory_id: str) -> bool:
        logger.debug(f"Entered into delete: id={memory_id}")
        rowid_row = self._conn.execute("SELECT rowid FROM memories WHERE id=?", (memory_id,)).fetchone()
        if rowid_row:
            self._conn.execute("DELETE FROM memory_vecs WHERE rowid=?", (rowid_row[0],))
        cursor = self._conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def delete_by_key(self, key: str, tier: MemoryTier | None = None) -> int:
        logger.debug(f"Entered into delete_by_key: key={key}, tier={tier}")
        if tier is not None:
            ids = [
                r[0] for r in self._conn.execute(
                    "SELECT id FROM memories WHERE key=? AND tier=?", (key, int(tier))
                ).fetchall()
            ]
        else:
            ids = [
                r[0] for r in self._conn.execute(
                    "SELECT id FROM memories WHERE key=?", (key,)
                ).fetchall()
            ]
        for mid in ids:
            self.delete(mid)
        return len(ids)

    def count(self, tier: MemoryTier | None = None) -> int:
        logger.debug(f"Entered into count: tier={tier}")
        if tier is not None:
            row = self._conn.execute("SELECT COUNT(*) FROM memories WHERE tier=?", (int(tier),)).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0] if row else 0

    def clear_session(self, session_id: str) -> int:
        logger.debug(f"Entered into clear_session: session_id={session_id}")
        ids = [
            r[0] for r in self._conn.execute(
                "SELECT id FROM memories WHERE session_id=? AND tier=?",
                (session_id, int(MemoryTier.SESSION)),
            ).fetchall()
        ]
        for mid in ids:
            self.delete(mid)
        return len(ids)

    def _row_to_entry(self, row: tuple) -> MemoryEntry:
        return MemoryEntry(
            id=row[0],
            tier=MemoryTier(row[1]),
            key=row[2],
            content=row[3],
            metadata=json.loads(row[4]) if row[4] else {},
            project_path=row[5] or "",
            session_id=row[6] or "",
            source=row[7] or "",
            created_at=row[8],
            updated_at=row[9],
            access_count=row[10],
        )


def _floats_to_blob(floats: list[float]) -> bytes:
    return struct.pack(f"{len(floats)}f", *floats)


def _blob_to_floats(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))

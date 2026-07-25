import json
import logging
import math
import sqlite3
import struct
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlite_vec

from elidia.config.settings import ELIDIA_HOME
from elidia.memory.embeddings import EmbeddingClient
from elidia.rag.chunker import chunk_text

logger = logging.getLogger(__name__)

RAG_DB_PATH = ELIDIA_HOME / "rag.db"
DEFAULT_EMBEDDING_DIM = 1024


@dataclass
class RagDocument:
    id: str = ""
    source: str = ""
    content_type: str = "text"
    chunk_index: int = 0
    total_chunks: int = 0
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    project_path: str = ""
    file_hash: str = ""
    created_at: float = 0.0
    start_line: int = 0
    end_line: int = 0


@dataclass
class SearchResult:
    document: RagDocument
    score: float = 0.0
    match_type: str = "vector"


class RagEngine:
    """Local RAG engine: chunk, embed, store in sqlite-vec, hybrid search."""

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        db_path: Path | None = None,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    ) -> None:
        logger.debug(f"Entered into RagEngine.__init__: db_path={db_path}")
        self._embeddings = embedding_client
        self._db_path = db_path or RAG_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_dim = embedding_dim
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        logger.debug(f"Entered into RagEngine.open: path={self._db_path}")
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._create_tables()

    def close(self) -> None:
        logger.debug("Entered into RagEngine.close")
        if self._conn:
            self._conn.close()
            self._conn = None

    def _create_tables(self) -> None:
        logger.debug("Entered into _create_tables")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS rag_docs (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                content_type TEXT DEFAULT 'text',
                chunk_index INTEGER DEFAULT 0,
                total_chunks INTEGER DEFAULT 1,
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                project_path TEXT DEFAULT '',
                file_hash TEXT DEFAULT '',
                created_at REAL NOT NULL,
                start_line INTEGER DEFAULT 0,
                end_line INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_rag_source ON rag_docs(source);
            CREATE INDEX IF NOT EXISTS idx_rag_project ON rag_docs(project_path);
            CREATE INDEX IF NOT EXISTS idx_rag_hash ON rag_docs(file_hash);
            CREATE INDEX IF NOT EXISTS idx_rag_content_type ON rag_docs(content_type);
        """)

        try:
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS rag_vecs USING vec0(embedding float[{self._embedding_dim}])"
            )
        except sqlite3.OperationalError as e:
            if "already exists" not in str(e).lower():
                raise
        self._conn.commit()

    async def ingest(
        self,
        text: str,
        source: str,
        content_type: str = "text",
        project_path: str = "",
        file_hash: str = "",
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> list[str]:
        logger.debug(f"Entered into ingest: source={source}, len={len(text)}, type={content_type}")

        if file_hash:
            existing = self._conn.execute(
                "SELECT id FROM rag_docs WHERE file_hash=? LIMIT 1", (file_hash,)
            ).fetchone()
            if existing:
                logger.info(f"Skipping already-ingested file: {source} (hash={file_hash})")
                return []

        chunks = chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap, source=source, content_type=content_type)
        if not chunks:
            return []

        chunk_texts = [c[0] for c in chunks]
        embeddings = await self._embeddings.embed(chunk_texts)

        ids: list[str] = []
        now = time.time()

        for i, ((chunk_content, chunk_meta), embedding) in enumerate(zip(chunks, embeddings)):
            doc_id = str(uuid.uuid4())

            self._conn.execute(
                """INSERT INTO rag_docs (id, source, content_type, chunk_index, total_chunks,
                   content, metadata, project_path, file_hash, created_at, start_line, end_line)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc_id, source, content_type, chunk_meta.chunk_index,
                    chunk_meta.total_chunks, chunk_content,
                    json.dumps({"source": source, "chunk": i}, default=str),
                    project_path, file_hash, now,
                    chunk_meta.start_line, chunk_meta.end_line,
                ),
            )

            blob = struct.pack(f"{len(embedding)}f", *embedding)
            self._conn.execute(
                "INSERT INTO rag_vecs(rowid, embedding) VALUES ((SELECT rowid FROM rag_docs WHERE id=?), ?)",
                (doc_id, blob),
            )

            ids.append(doc_id)

        self._conn.commit()
        logger.info(f"Ingested {len(ids)} chunks from {source}")
        return ids

    async def search(
        self,
        query: str,
        limit: int = 10,
        project_path: str = "",
        content_types: list[str] | None = None,
        hybrid: bool = True,
    ) -> list[SearchResult]:
        logger.debug(f"Entered into search: query={query!r}, limit={limit}, hybrid={hybrid}")

        results: list[SearchResult] = []

        if hybrid:
            text_results = self._search_bm25(query, limit=limit, project_path=project_path, content_types=content_types)
            results.extend(text_results)

        try:
            query_embedding = await self._embeddings.embed_single(query)
            vec_results = self._search_vector(
                query_embedding, limit=limit, project_path=project_path, content_types=content_types
            )

            seen_ids = {r.document.id for r in results}
            for r in vec_results:
                if r.document.id not in seen_ids:
                    results.append(r)
                    seen_ids.add(r.document.id)
                else:
                    for existing in results:
                        if existing.document.id == r.document.id:
                            existing.score = max(existing.score, r.score)
                            existing.match_type = "hybrid"
                            break
        except Exception as e:
            logger.warning(f"Vector search failed (text search still used): {e}")

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def _search_bm25(
        self,
        query: str,
        limit: int = 10,
        project_path: str = "",
        content_types: list[str] | None = None,
    ) -> list[SearchResult]:
        logger.debug(f"Entered into _search_bm25: query={query!r}")
        conditions = ["content LIKE ?"]
        params: list[Any] = [f"%{query}%"]

        if project_path:
            conditions.append("project_path = ?")
            params.append(project_path)
        if content_types:
            placeholders = ",".join("?" for _ in content_types)
            conditions.append(f"content_type IN ({placeholders})")
            params.extend(content_types)

        where = " AND ".join(conditions)
        params.append(limit * 2)

        rows = self._conn.execute(
            f"SELECT * FROM rag_docs WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()

        results: list[SearchResult] = []
        query_lower = query.lower()
        query_terms = set(query_lower.split())

        for row in rows:
            doc = self._row_to_doc(row)
            content_lower = doc.content.lower()
            term_hits = sum(1 for t in query_terms if t in content_lower)
            score = term_hits / max(len(query_terms), 1)
            score *= min(1.0, 200 / max(len(doc.content), 1))

            age_hours = (time.time() - doc.created_at) / 3600
            recency_boost = 1.0 / (1.0 + math.log1p(age_hours / 24))
            score *= (1.0 + 0.2 * recency_boost)

            results.append(SearchResult(document=doc, score=score, match_type="text"))

        return results

    def _search_vector(
        self,
        query_embedding: list[float],
        limit: int = 10,
        project_path: str = "",
        content_types: list[str] | None = None,
    ) -> list[SearchResult]:
        logger.debug(f"Entered into _search_vector: dim={len(query_embedding)}")
        blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)

        rows = self._conn.execute(
            """
            SELECT d.*, v.distance
            FROM rag_vecs v
            JOIN rag_docs d ON d.rowid = v.rowid
            WHERE v.embedding MATCH ?
            AND k = ?
            ORDER BY v.distance
            """,
            (blob, limit * 2),
        ).fetchall()

        results: list[SearchResult] = []
        for row in rows:
            distance = row[-1]
            doc = self._row_to_doc(row[:-1])

            if project_path and doc.project_path != project_path:
                continue
            if content_types and doc.content_type not in content_types:
                continue

            score = max(0.0, 1.0 - distance)
            results.append(SearchResult(document=doc, score=score, match_type="vector"))

        return results[:limit]

    def delete_source(self, source: str) -> int:
        logger.debug(f"Entered into delete_source: source={source}")
        rowids = [
            r[0] for r in self._conn.execute(
                "SELECT rowid FROM rag_docs WHERE source=?", (source,)
            ).fetchall()
        ]
        for rid in rowids:
            self._conn.execute("DELETE FROM rag_vecs WHERE rowid=?", (rid,))
        cursor = self._conn.execute("DELETE FROM rag_docs WHERE source=?", (source,))
        self._conn.commit()
        return cursor.rowcount

    def delete_by_hash(self, file_hash: str) -> int:
        logger.debug(f"Entered into delete_by_hash: hash={file_hash}")
        rowids = [
            r[0] for r in self._conn.execute(
                "SELECT rowid FROM rag_docs WHERE file_hash=?", (file_hash,)
            ).fetchall()
        ]
        for rid in rowids:
            self._conn.execute("DELETE FROM rag_vecs WHERE rowid=?", (rid,))
        cursor = self._conn.execute("DELETE FROM rag_docs WHERE file_hash=?", (file_hash,))
        self._conn.commit()
        return cursor.rowcount

    def count_documents(self, project_path: str = "") -> dict[str, int]:
        logger.debug(f"Entered into count_documents: project_path={project_path}")
        if project_path:
            row = self._conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT source) FROM rag_docs WHERE project_path=?",
                (project_path,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT source) FROM rag_docs"
            ).fetchone()
        return {"chunks": row[0], "sources": row[1]}

    def _row_to_doc(self, row: tuple) -> RagDocument:
        return RagDocument(
            id=row[0],
            source=row[1],
            content_type=row[2],
            chunk_index=row[3],
            total_chunks=row[4],
            content=row[5],
            metadata=json.loads(row[6]) if row[6] else {},
            project_path=row[7] or "",
            file_hash=row[8] or "",
            created_at=row[9],
            start_line=row[10],
            end_line=row[11],
        )

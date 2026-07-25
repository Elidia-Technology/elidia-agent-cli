import logging
from typing import Any

from elidia.db.database import Database

logger = logging.getLogger(__name__)


class HistorySearch:
    """Searches across all chat sessions for messages matching a query."""

    def __init__(self, db: Database) -> None:
        logger.debug("Entered into HistorySearch.__init__")
        self._db = db

    def search(
        self,
        query: str,
        limit: int = 20,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        logger.debug(f"Entered into search: query={query!r}, limit={limit}")
        conditions = ["m.content LIKE ?"]
        params: list[Any] = [f"%{query}%"]

        if role:
            conditions.append("m.role = ?")
            params.append(role)

        where = " AND ".join(conditions)
        params.append(limit)

        with self._db.cursor() as cur:
            rows = cur.execute(
                f"""SELECT m.id, m.session_id, m.role, m.content, m.model,
                           m.tokens_in, m.tokens_out, m.created_at,
                           s.title
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE {where}
                    ORDER BY m.created_at DESC
                    LIMIT ?""",
                params,
            ).fetchall()

        return [
            {
                "id": r[0],
                "session_id": r[1],
                "role": r[2],
                "content": r[3],
                "model": r[4] or "",
                "tokens_in": r[5] or 0,
                "tokens_out": r[6] or 0,
                "created_at": r[7],
                "session_title": r[8] or "",
            }
            for r in rows
        ]

    def browse(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        logger.debug(f"Entered into browse: session_id={session_id}")
        with self._db.cursor() as cur:
            rows = cur.execute(
                """SELECT id, role, content, model, tokens_in, tokens_out, created_at
                   FROM messages
                   WHERE session_id = ?
                   ORDER BY created_at ASC
                   LIMIT ? OFFSET ?""",
                (session_id, limit, offset),
            ).fetchall()

        return [
            {
                "id": r[0],
                "role": r[1],
                "content": r[2],
                "model": r[3] or "",
                "tokens_in": r[4] or 0,
                "tokens_out": r[5] or 0,
                "created_at": r[6],
            }
            for r in rows
        ]

    def get_session_stats(self) -> list[dict[str, Any]]:
        logger.debug("Entered into get_session_stats")
        with self._db.cursor() as cur:
            rows = cur.execute(
                """SELECT s.id, s.title, s.mode, s.model, s.created_at, s.updated_at,
                          COUNT(m.id) as msg_count,
                          COALESCE(SUM(m.tokens_in), 0) as total_tokens_in,
                          COALESCE(SUM(m.tokens_out), 0) as total_tokens_out
                   FROM sessions s
                   LEFT JOIN messages m ON m.session_id = s.id
                   GROUP BY s.id
                   ORDER BY s.updated_at DESC
                   LIMIT 50"""
            ).fetchall()

        return [
            {
                "id": r[0],
                "title": r[1] or "",
                "mode": r[2] or "chat",
                "model": r[3] or "auto",
                "created_at": r[4],
                "updated_at": r[5],
                "message_count": r[6],
                "total_tokens_in": r[7],
                "total_tokens_out": r[8],
            }
            for r in rows
        ]

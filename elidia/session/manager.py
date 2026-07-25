import logging
import uuid

from elidia.db.database import Database

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, db: Database):
        logger.debug("Entered into SessionManager.__init__: initializing session manager")
        self._db = db

    def create_session(self, title: str = "New Session", model: str | None = None, mode: str = "chat") -> str:
        logger.debug(f"Entered into create_session: title={title}, model={model}, mode={mode}")
        sid = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO sessions (id, title, model, mode) VALUES (?, ?, ?, ?)",
            (sid, title, model, mode),
        )
        return sid

    def list_sessions(self, limit: int = 20) -> list[dict]:
        logger.debug(f"Entered into list_sessions: limit={limit}")
        rows = self._db.execute(
            "SELECT id, title, model, mode, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict | None:
        logger.debug(f"Entered into get_session: session_id={session_id}")
        rows = self._db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return dict(rows[0]) if rows else None

    def update_title(self, session_id: str, title: str) -> None:
        logger.debug(f"Entered into update_title: session_id={session_id}, title={title}")
        self._db.execute(
            "UPDATE sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title, session_id),
        )

    def delete_session(self, session_id: str) -> None:
        logger.debug(f"Entered into delete_session: session_id={session_id}")
        self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_dt: float = 0.0,
    ) -> str:
        logger.debug(f"Entered into add_message: session_id={session_id}, role={role}, model={model}")
        mid = str(uuid.uuid4())
        self._db.execute(
            "INSERT INTO messages (id, session_id, role, content, model, tokens_in, tokens_out, cost_dt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, session_id, role, content, model, tokens_in, tokens_out, cost_dt),
        )
        self._db.execute(
            "UPDATE sessions SET updated_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
        return mid

    def get_messages(self, session_id: str, limit: int = 100) -> list[dict]:
        logger.debug(f"Entered into get_messages: session_id={session_id}, limit={limit}")
        rows = self._db.execute(
            "SELECT role, content, model, tokens_in, tokens_out, cost_dt, created_at FROM messages "
            "WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        )
        return [dict(r) for r in rows]

    def get_last_session_id(self) -> str | None:
        logger.debug("Entered into get_last_session_id: fetching most recently updated session")
        rows = self._db.execute("SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1")
        return rows[0]["id"] if rows else None

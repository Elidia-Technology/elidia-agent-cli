"""Database Skills — read-only SQL against an external database.

v1 is deliberately read-only: write/DDL access is a separate, later
capability requiring its own design pass (per the master plan's security
note on this category), not bundled in here. The connection itself is
held in-process only (module-level, not persisted to disk/keyring) —
re-supplied each session, same spirit as not silently retaining a
credential beyond its immediate use.

Statement validation uses sqlparse's actual statement-type classification,
not a keyword blocklist — a blocklist can be defeated by comments, casing,
or stacked statements (`SELECT 1; DROP TABLE users`); sqlparse correctly
splits stacked statements and classifies each one, so both are caught.
"""
from __future__ import annotations

import logging
from typing import Any

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

MAX_ROWS = 500
QUERY_TIMEOUT_SECONDS = 15


class DatabaseSession:
    """Holds one live SQLAlchemy engine for the current REPL session."""

    def __init__(self) -> None:
        logger.debug("Entered into DatabaseSession.__init__")
        self._engine = None
        self._connection_string_masked = ""

    def connect(self, connection_string: str) -> None:
        logger.debug("Entered into DatabaseSession.connect")
        from sqlalchemy import create_engine

        if self._engine is not None:
            self._engine.dispose()
        self._engine = create_engine(connection_string, pool_pre_ping=True)
        self._connection_string_masked = _mask_credentials(connection_string)

    def require_engine(self):
        if self._engine is None:
            raise RuntimeError("No database connected — call db_connect(connection_string) first")
        return self._engine

    def close(self) -> None:
        logger.debug("Entered into DatabaseSession.close")
        if self._engine is not None:
            self._engine.dispose()
        self._engine = None
        self._connection_string_masked = ""


def _mask_credentials(connection_string: str) -> str:
    """Strip a password out of a connection string for safe display/logging."""
    import re
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:****@", connection_string)


_session: DatabaseSession | None = None


def _get_session() -> DatabaseSession:
    global _session
    if _session is None:
        _session = DatabaseSession()
    return _session


def close_database_session() -> None:
    """Call on /new or CLI exit to release the connection."""
    logger.debug("Entered into close_database_session")
    global _session
    if _session is not None:
        _session.close()
        _session = None


def validate_select_only(sql: str) -> str | None:
    """Return an error message if `sql` is anything but a single SELECT statement, else None."""
    try:
        import sqlparse
    except ImportError:
        return "sqlparse not installed: pip install elidia-agent-cli[database]"

    statements = [s for s in sqlparse.parse(sql) if s.token_first(skip_cm=True) is not None]
    if len(statements) == 0:
        return "Empty query"
    if len(statements) > 1:
        return "Only a single statement is allowed (stacked/multi-statement queries are rejected)"

    stmt_type = statements[0].get_type()
    if stmt_type != "SELECT":
        return f"Only SELECT queries are allowed in this tool (got: {stmt_type or 'UNKNOWN'}). Write/DDL access is not implemented."
    return None


async def _db_connect(connection_string: str) -> ToolResult:
    logger.debug("Entered into _db_connect")
    try:
        session = _get_session()
        session.connect(connection_string)
        from sqlalchemy import text
        with session.require_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return ToolResult(content=f"Connected: {session._connection_string_masked}")
    except Exception as e:
        return ToolResult(content=f"Connection failed: {e}", is_error=True)


async def _db_query(sql: str) -> ToolResult:
    logger.debug(f"Entered into _db_query: sql_len={len(sql)}")
    err = validate_select_only(sql)
    if err:
        return ToolResult(content=err, is_error=True)

    try:
        session = _get_session()
        engine = session.require_engine()
    except RuntimeError as e:
        return ToolResult(content=str(e), is_error=True)

    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execution_options(timeout=QUERY_TIMEOUT_SECONDS).execute(text(sql))
            columns = list(result.keys())
            rows = result.fetchmany(MAX_ROWS)
            extra_row = result.fetchone()

        if not rows:
            return ToolResult(content="Query returned no rows.", metadata={"columns": columns, "row_count": 0})

        lines = [" | ".join(columns)]
        for row in rows:
            lines.append(" | ".join(str(v) for v in row))
        content = "\n".join(lines)
        if extra_row is not None:
            content += f"\n... (truncated at {MAX_ROWS} rows)"

        return ToolResult(content=content, metadata={"columns": columns, "row_count": len(rows)})
    except Exception as e:
        return ToolResult(content=f"Query failed: {e}", is_error=True)


async def _db_list_tables() -> ToolResult:
    logger.debug("Entered into _db_list_tables")
    try:
        session = _get_session()
        engine = session.require_engine()
    except RuntimeError as e:
        return ToolResult(content=str(e), is_error=True)

    try:
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        if not tables:
            return ToolResult(content="No tables found.")
        return ToolResult(content="\n".join(sorted(tables)), metadata={"count": len(tables)})
    except Exception as e:
        return ToolResult(content=f"Could not list tables: {e}", is_error=True)


async def _db_describe_table(table_name: str) -> ToolResult:
    logger.debug(f"Entered into _db_describe_table: table_name={table_name}")
    try:
        session = _get_session()
        engine = session.require_engine()
    except RuntimeError as e:
        return ToolResult(content=str(e), is_error=True)

    try:
        from sqlalchemy import inspect
        inspector = inspect(engine)
        if table_name not in inspector.get_table_names():
            return ToolResult(content=f"Table not found: {table_name}", is_error=True)

        columns: list[dict[str, Any]] = inspector.get_columns(table_name)
        lines = [f"{c['name']}: {c['type']}" + ("" if c.get("nullable", True) else " NOT NULL") for c in columns]

        pk = inspector.get_pk_constraint(table_name).get("constrained_columns", [])
        if pk:
            lines.append(f"Primary key: {', '.join(pk)}")

        return ToolResult(content="\n".join(lines), metadata={"column_count": len(columns)})
    except Exception as e:
        return ToolResult(content=f"Could not describe {table_name}: {e}", is_error=True)


def register_database_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_database_tools")

    registry.register(ToolDefinition(
        name="db_connect", description="Connect to a database (read-only session; call before db_query)",
        parameters={"type": "object", "properties": {
            "connection_string": {
                "type": "string",
                "description": "SQLAlchemy connection string, e.g. postgresql://user:pass@host/dbname or sqlite:///path.db",
            },
        }, "required": ["connection_string"]},
        handler=_db_connect, category="database",
    ))
    registry.register(ToolDefinition(
        name="db_query", description="Run a read-only SELECT query against the connected database",
        parameters={"type": "object", "properties": {
            "sql": {"type": "string", "description": "A single SELECT statement (no writes, no DDL, no stacked statements)"},
        }, "required": ["sql"]},
        handler=_db_query, category="database",
    ))
    registry.register(ToolDefinition(
        name="db_list_tables", description="List tables in the connected database",
        parameters={"type": "object", "properties": {}},
        handler=_db_list_tables, category="database",
    ))
    registry.register(ToolDefinition(
        name="db_describe_table", description="Show columns and primary key for a table",
        parameters={"type": "object", "properties": {
            "table_name": {"type": "string", "description": "Name of the table to describe"},
        }, "required": ["table_name"]},
        handler=_db_describe_table, category="database",
    ))

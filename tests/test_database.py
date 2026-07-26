"""Tests for elidia.tools.database — read-only SQL against an external database."""
import sqlite3
from pathlib import Path

import pytest

from elidia.permissions.manager import ACTION_TIERS, NEVER_PROMOTE, PermissionTier
from elidia.tools import ToolRegistry, create_default_registry
from elidia.tools.database import (
    _db_connect,
    _db_describe_table,
    _db_list_tables,
    _db_query,
    close_database_session,
    register_database_tools,
    validate_select_only,
)


@pytest.fixture
def sqlite_db(tmp_dir: Path) -> str:
    """A real SQLite fixture DB with known data."""
    db_path = tmp_dir / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, score INTEGER)")
    conn.execute("INSERT INTO users (name, score) VALUES ('Alice', 95)")
    conn.execute("INSERT INTO users (name, score) VALUES ('Bob', 88)")
    conn.commit()
    conn.close()
    yield f"sqlite:///{db_path}"
    close_database_session()


class TestRegistration:
    def test_registers_four_tools(self):
        registry = ToolRegistry()
        register_database_tools(registry)
        names = {t.name for t in registry.list_tools()}
        assert names == {"db_connect", "db_query", "db_list_tables", "db_describe_table"}

    def test_wired_into_default_registry(self):
        registry = create_default_registry()
        assert registry.get("db_query") is not None


class TestPermissionTiering:
    def test_db_query_is_every_time(self):
        assert ACTION_TIERS["db_query"] == PermissionTier.EVERY_TIME

    def test_db_query_never_promotes(self):
        assert "db_query" in NEVER_PROMOTE


class TestValidateSelectOnly:
    @pytest.mark.parametrize("sql", [
        "SELECT * FROM users",
        "select * from users",
        "  SELECT id, name FROM users WHERE score > 90",
        "WITH ranked AS (SELECT * FROM users) SELECT * FROM ranked",
        "/* comment */ SELECT 1",
    ])
    def test_allows_select(self, sql):
        assert validate_select_only(sql) is None

    @pytest.mark.parametrize("sql", [
        "DROP TABLE users",
        "DELETE FROM users",
        "INSERT INTO users (name) VALUES ('Eve')",
        "UPDATE users SET score = 100",
        "TRUNCATE TABLE users",
        "CREATE TABLE evil (id INT)",
        "ALTER TABLE users ADD COLUMN x INT",
    ])
    def test_rejects_writes_and_ddl(self, sql):
        err = validate_select_only(sql)
        assert err is not None
        assert "SELECT" in err

    def test_rejects_stacked_statements(self):
        err = validate_select_only("SELECT 1; DROP TABLE users")
        assert err is not None
        assert "single statement" in err.lower()

    def test_rejects_select_hiding_a_drop_via_stacking(self):
        # The exact injection shape the naive-blocklist approach would miss
        # if it only checked the first token / a substring for "drop".
        err = validate_select_only("SELECT * FROM users; -- looks safe\nDROP TABLE users")
        assert err is not None

    def test_rejects_empty_query(self):
        err = validate_select_only("")
        assert err is not None


class TestDbConnectAndQuery:
    @pytest.mark.asyncio
    async def test_connect_success(self, sqlite_db):
        result = await _db_connect(sqlite_db)
        assert not result.is_error
        assert "Connected" in result.content

    @pytest.mark.asyncio
    async def test_connect_masks_credentials_in_response(self):
        await _db_connect("postgresql://admin:supersecret@localhost/mydb")
        # Connection itself will fail (no real postgres), but the masking
        # happens before the connection attempt — check via the session directly.
        from elidia.tools.database import _get_session
        assert "supersecret" not in _get_session()._connection_string_masked
        close_database_session()

    @pytest.mark.asyncio
    async def test_query_without_connect_is_error(self):
        close_database_session()
        result = await _db_query("SELECT 1")
        assert result.is_error
        assert "connect" in result.content.lower()

    @pytest.mark.asyncio
    async def test_query_returns_real_data(self, sqlite_db):
        await _db_connect(sqlite_db)
        result = await _db_query("SELECT name, score FROM users ORDER BY score DESC")
        assert not result.is_error
        assert "Alice" in result.content
        assert "95" in result.content
        assert result.metadata["row_count"] == 2

    @pytest.mark.asyncio
    async def test_query_rejects_write_before_ever_touching_db(self, sqlite_db):
        await _db_connect(sqlite_db)
        result = await _db_query("DELETE FROM users")
        assert result.is_error

        # Confirm the reject actually happened before execution — data still intact.
        verify = await _db_query("SELECT COUNT(*) as c FROM users")
        assert "2" in verify.content

    @pytest.mark.asyncio
    async def test_list_tables(self, sqlite_db):
        await _db_connect(sqlite_db)
        result = await _db_list_tables()
        assert not result.is_error
        assert "users" in result.content

    @pytest.mark.asyncio
    async def test_describe_table(self, sqlite_db):
        await _db_connect(sqlite_db)
        result = await _db_describe_table("users")
        assert not result.is_error
        assert "name" in result.content
        assert "score" in result.content

    @pytest.mark.asyncio
    async def test_describe_missing_table(self, sqlite_db):
        await _db_connect(sqlite_db)
        result = await _db_describe_table("nonexistent")
        assert result.is_error

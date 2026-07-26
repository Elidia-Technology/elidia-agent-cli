"""Tests for elidia.tools.rag — the read side of RAG (search over ingested content).

Ingestion/search correctness is exercised against a real RagEngine (real
sqlite-vec DB, real FTS5, real chunking) with a fake embedding client
(deterministic vectors, no network) — the same "real storage, fake network
edge" split used for the email tests' local SMTP server.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from elidia.permissions.manager import ACTION_TIERS, PermissionTier
from elidia.rag.engine import RagEngine, _build_fts_query
from elidia.tools import ToolRegistry, create_default_registry
from elidia.tools.rag import (
    RagSession,
    _rag_list_sources,
    _rag_search,
    close_rag_session,
    register_rag_tools,
)
import elidia.tools.rag as rag_tool_module


class _FakeEmbeddingClient:
    """Deterministic fake — same text always maps to the same vector, so
    exact-content search behaves predictably without any network call."""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed(self, texts: list[str], model: str = "") -> list[list[float]]:
        return [await self.embed_single(t) for t in texts]

    async def embed_single(self, text: str, model: str = "") -> list[float]:
        seed = sum(ord(c) for c in text) or 1
        return [((seed * (i + 1)) % 997) / 997.0 for i in range(self._dim)]


@pytest.fixture
def rag_engine(tmp_dir: Path):
    engine = RagEngine(_FakeEmbeddingClient(), db_path=tmp_dir / "rag.db")
    engine.open()
    yield engine
    engine.close()


@pytest.fixture(autouse=True)
def _reset_session():
    close_rag_session()
    yield
    close_rag_session()


class TestRegistration:
    def test_registers_two_tools(self):
        registry = ToolRegistry()
        register_rag_tools(registry)
        names = {t.name for t in registry.list_tools()}
        assert names == {"rag_search", "rag_list_sources"}

    def test_wired_into_default_registry(self):
        registry = create_default_registry()
        assert registry.get("rag_search") is not None
        assert registry.get("rag_list_sources") is not None


class TestPermissionTiering:
    def test_rag_search_is_auto(self):
        assert ACTION_TIERS["rag_search"] == PermissionTier.AUTO


class TestRagSearchNoCredentials:
    @pytest.mark.asyncio
    async def test_search_without_api_key_is_error(self):
        with patch("elidia.auth.keychain.get_api_key", return_value=None):
            result = await _rag_search("anything")
        assert result.is_error
        assert "auth login" in result.content


class TestBuildFtsQuery:
    """A real bug, found live: an unquoted term containing a hyphen (e.g.
    'on-call') crashed FTS5's MATCH parser with "no such column: call" —
    FTS5 treats '-' as query syntax even inside a bareword. Quoting every
    term fixes this class of bug rather than one character at a time."""

    def test_hyphenated_term_is_quoted(self):
        assert _build_fts_query("on-call") == '"on-call"*'

    def test_colon_and_hyphen_terms_do_not_crash_sqlite(self, tmp_dir: Path):
        # The real regression check: actually run it through FTS5, not just
        # inspect the string — a plausible-looking fix can still not parse.
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
        conn.execute("INSERT INTO t(content) VALUES ('the on-call path uses ratio 2:1 for escalation')")
        query = _build_fts_query("on-call escalation ratio 2:1")
        rows = conn.execute("SELECT * FROM t WHERE t MATCH ?", [query]).fetchall()
        assert rows == [("the on-call path uses ratio 2:1 for escalation",)]

    def test_multi_word_query_ands_quoted_terms(self):
        assert _build_fts_query("hello world") == '"hello"* AND "world"*'

    def test_short_term_not_prefixed(self):
        assert _build_fts_query("is") == '"is"'

    def test_literal_quote_in_term_is_doubled(self):
        assert _build_fts_query('say "hi"') == '"say"* AND """hi"""*'

    def test_empty_query(self):
        assert _build_fts_query("") == ""


class TestRagSearchAndList:
    @pytest.mark.asyncio
    async def test_search_finds_ingested_content(self, rag_engine):
        await rag_engine.ingest(
            text="The quarterly revenue report shows a 12% increase in Q3.",
            source="report.txt",
        )

        session = RagSession()
        session.set_engine(rag_engine)
        with patch.object(rag_tool_module, "_get_session", return_value=session):
            result = await _rag_search("quarterly revenue")

        assert not result.is_error
        assert "12% increase" in result.content
        assert "report.txt" in result.content

    @pytest.mark.asyncio
    async def test_search_no_matches_is_not_an_error(self, rag_engine):
        session = RagSession()
        session.set_engine(rag_engine)
        with patch.object(rag_tool_module, "_get_session", return_value=session):
            result = await _rag_search("nothing has ever been ingested here")

        assert not result.is_error
        assert "No matching content" in result.content

    @pytest.mark.asyncio
    async def test_list_sources_reports_real_counts(self, rag_engine):
        await rag_engine.ingest(text="Some document content here.", source="a.txt")
        await rag_engine.ingest(text="Some other document content.", source="b.txt")

        session = RagSession()
        session.set_engine(rag_engine)
        with patch.object(rag_tool_module, "_get_session", return_value=session):
            result = await _rag_list_sources()

        assert "2 source(s)" in result.content

    @pytest.mark.asyncio
    async def test_list_sources_empty_store(self, rag_engine):
        session = RagSession()
        session.set_engine(rag_engine)
        with patch.object(rag_tool_module, "_get_session", return_value=session):
            result = await _rag_list_sources()

        assert "Nothing has been ingested" in result.content


class TestRagEngineClearAll:
    @pytest.mark.asyncio
    async def test_clear_all_removes_everything(self, rag_engine):
        await rag_engine.ingest(text="Content to be deleted.", source="a.txt")
        await rag_engine.ingest(text="More content to be deleted.", source="b.txt")
        assert rag_engine.count_documents()["sources"] == 2

        deleted = rag_engine.clear_all()

        assert deleted == 2
        assert rag_engine.count_documents() == {"chunks": 0, "sources": 0}

    @pytest.mark.asyncio
    async def test_clear_all_on_empty_store_returns_zero(self, rag_engine):
        assert rag_engine.clear_all() == 0


class TestAutoIngestFileContext:
    """elidia/cli/main.py::_build_file_context — large files get indexed
    into RAG plus a preview instead of being blindly truncated (or, for
    files >1MB, instead of being dropped entirely). See AIUT-2141."""

    @pytest.mark.asyncio
    async def test_small_file_stays_fully_inlined_no_ingest_attempted(self, tmp_dir: Path):
        from elidia.cli.main import _build_file_context

        path = tmp_dir / "small.txt"
        path.write_text("short content, well under the auto-ingest threshold", encoding="utf-8")

        with patch("elidia.cli.main._auto_ingest_file", new=AsyncMock(side_effect=AssertionError("should not be called"))):
            ctx = await _build_file_context((str(path),))

        assert "short content, well under the auto-ingest threshold" in ctx
        assert "indexed into" not in ctx

    @pytest.mark.asyncio
    async def test_large_file_with_key_gets_preview_and_rag_note(self, tmp_dir: Path):
        from elidia.cli.main import _AUTO_INGEST_THRESHOLD, _build_file_context

        content = "x" * (_AUTO_INGEST_THRESHOLD + 5_000)
        path = tmp_dir / "big.txt"
        path.write_text(content, encoding="utf-8")

        with patch("elidia.cli.main._auto_ingest_file", new=AsyncMock(return_value=True)) as mock_ingest:
            ctx = await _build_file_context((str(path),))

        mock_ingest.assert_awaited_once()
        assert "indexed into RAG" in ctx
        assert "rag_search" in ctx
        # Preview is capped at the threshold, not the full oversized content.
        assert ctx.count("x") < len(content)

    @pytest.mark.asyncio
    async def test_large_file_without_key_falls_back_to_plain_inline(self, tmp_dir: Path):
        from elidia.cli.main import _AUTO_INGEST_THRESHOLD, _build_file_context

        content = "y" * (_AUTO_INGEST_THRESHOLD + 5_000)
        path = tmp_dir / "big.txt"
        path.write_text(content, encoding="utf-8")

        with patch("elidia.cli.main._auto_ingest_file", new=AsyncMock(return_value=False)):
            ctx = await _build_file_context((str(path),))

        assert "indexed into RAG" not in ctx
        assert content in ctx  # falls back to the pre-auto-ingest full-inline behavior

    @pytest.mark.asyncio
    async def test_over_1mb_file_with_key_gets_indexed_not_dropped(self, tmp_dir: Path):
        from elidia.cli.main import _build_file_context

        path = tmp_dir / "huge.txt"
        path.write_text("z" * (1_100_000), encoding="utf-8")

        with patch("elidia.cli.main._auto_ingest_file", new=AsyncMock(return_value=True)) as mock_ingest:
            ctx = await _build_file_context((str(path),))

        mock_ingest.assert_awaited_once()
        assert "indexed into the local RAG store" in ctx
        assert "too large to include" not in ctx

    @pytest.mark.asyncio
    async def test_over_1mb_file_without_key_reports_too_large(self, tmp_dir: Path):
        from elidia.cli.main import _build_file_context

        path = tmp_dir / "huge.txt"
        path.write_text("z" * (1_100_000), encoding="utf-8")

        with patch("elidia.cli.main._auto_ingest_file", new=AsyncMock(return_value=False)):
            ctx = await _build_file_context((str(path),))

        assert "too large to include" in ctx

    @pytest.mark.asyncio
    async def test_auto_ingest_file_returns_false_without_api_key(self, tmp_dir: Path):
        from elidia.cli.main import _auto_ingest_file

        path = tmp_dir / "f.txt"
        path.write_text("content", encoding="utf-8")

        with patch("elidia.auth.keychain.get_api_key", return_value=None):
            result = await _auto_ingest_file(path, "content")

        assert result is False


class TestSessionLifecycle:
    def test_close_rag_session_is_safe_when_never_opened(self):
        close_rag_session()  # must not raise

    @pytest.mark.asyncio
    async def test_session_reused_across_calls(self, rag_engine):
        session = RagSession()
        session.set_engine(rag_engine)
        assert session.engine is rag_engine
        session.close()
        assert session.engine is None


class TestAskCommandFileFlag:
    """Real bug, found live: `elidia ask` never read -f/--file at all (own
    option or the parent group's), so both `elidia ask -f x.txt "..."` and
    `elidia --file x.txt ask "..."` silently ignored the file — the model
    was told nothing was attached. Fixed by giving `ask` its own -f/--file
    (mirroring how -i/--image already worked) plus the parent fallback."""

    def test_ask_passes_its_own_file_flag_through(self, tmp_dir: Path):
        from click.testing import CliRunner

        from elidia.cli.main import cli

        target = tmp_dir / "doc.txt"
        target.write_text("content", encoding="utf-8")

        with patch("elidia.cli.main._one_shot", new=AsyncMock()) as mock_one_shot:
            result = CliRunner().invoke(cli, ["ask", "-f", str(target), "what is in the file?"])

        assert result.exit_code == 0, result.output
        mock_one_shot.assert_awaited_once()
        _, kwargs = mock_one_shot.call_args
        assert kwargs["files"] == (str(target),)

    def test_ask_falls_back_to_parent_group_file_flag(self, tmp_dir: Path):
        from click.testing import CliRunner

        from elidia.cli.main import cli

        target = tmp_dir / "doc.txt"
        target.write_text("content", encoding="utf-8")

        with patch("elidia.cli.main._one_shot", new=AsyncMock()) as mock_one_shot:
            result = CliRunner().invoke(cli, ["--file", str(target), "ask", "what is in the file?"])

        assert result.exit_code == 0, result.output
        mock_one_shot.assert_awaited_once()
        _, kwargs = mock_one_shot.call_args
        assert kwargs["files"] == (str(target),)

    def test_ask_with_no_file_flag_passes_empty_tuple(self):
        from click.testing import CliRunner

        from elidia.cli.main import cli

        with patch("elidia.cli.main._one_shot", new=AsyncMock()) as mock_one_shot:
            result = CliRunner().invoke(cli, ["ask", "just a question"])

        assert result.exit_code == 0, result.output
        _, kwargs = mock_one_shot.call_args
        assert kwargs["files"] == ()

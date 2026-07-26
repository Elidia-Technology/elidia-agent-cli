"""RAG Skill — local semantic search over ingested project files.

Ingestion happens through three entry points, deliberately not through
this tool: `elidia rag ingest <path>` (power users/automation), auto-ingest
of large files passed via -f/--file (see cli/main.py::_build_file_context),
and the REPL's `/rag ingest <path>`. Ingestion calls the embeddings API
per chunk, so it stays an explicit user action rather than something the
agent can trigger on its own mid-conversation — the same reasoning as why
email-login is CLI-only and not agent-invocable.

This module is the read side: it lets the agent search whatever has
already been ingested as part of its own reasoning, instead of requiring
the user to paste file contents into every message.
"""
from __future__ import annotations

import logging

from elidia.tools.base import ToolDefinition, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 5


class RagSession:
    """Holds one live RagEngine connection for the current process."""

    def __init__(self) -> None:
        logger.debug("Entered into RagSession.__init__")
        self._engine = None

    def set_engine(self, engine) -> None:
        self._engine = engine

    @property
    def engine(self):
        return self._engine

    def close(self) -> None:
        logger.debug("Entered into RagSession.close")
        if self._engine is not None:
            self._engine.close()
        self._engine = None


_session: RagSession | None = None


def _get_session() -> RagSession:
    global _session
    if _session is None:
        _session = RagSession()
    return _session


def close_rag_session() -> None:
    """Call on /new or CLI exit to release the connection."""
    logger.debug("Entered into close_rag_session")
    global _session
    if _session is not None:
        _session.close()
        _session = None


async def _ensure_engine():
    """Lazily construct+open a RagEngine using the stored API key, reused
    across calls within the process (mirrors DatabaseSession/BrowserSession)."""
    logger.debug("Entered into _ensure_engine")
    session = _get_session()
    if session.engine is not None:
        return session.engine

    from elidia.auth.keychain import get_api_key
    from elidia.memory.embeddings import EmbeddingClient
    from elidia.rag.engine import RagEngine

    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "No API key configured — run 'elidia auth login' first (RAG search needs it for embeddings)"
        )

    engine = RagEngine(EmbeddingClient(api_key=api_key))
    engine.open()
    session.set_engine(engine)
    return engine


async def _rag_search(query: str, limit: int = DEFAULT_LIMIT) -> ToolResult:
    logger.debug(f"Entered into _rag_search: query={query!r}, limit={limit}")
    try:
        engine = await _ensure_engine()
    except RuntimeError as e:
        return ToolResult(content=str(e), is_error=True)

    try:
        results = await engine.search(query, limit=limit)
    except Exception as e:
        return ToolResult(content=f"RAG search failed: {e}", is_error=True)

    if not results:
        return ToolResult(content="No matching content found in the ingested RAG store. Nothing has been ingested yet, or nothing matches this query.")

    lines = []
    for r in results:
        doc = r.document
        lines.append(
            f"[{doc.source} — chunk {doc.chunk_index + 1}/{doc.total_chunks}, score={r.score:.3f}]\n{doc.content}"
        )
    return ToolResult(content="\n\n---\n\n".join(lines))


async def _rag_list_sources() -> ToolResult:
    logger.debug("Entered into _rag_list_sources")
    try:
        engine = await _ensure_engine()
    except RuntimeError as e:
        return ToolResult(content=str(e), is_error=True)

    counts = engine.count_documents()
    if counts["sources"] == 0:
        return ToolResult(content="Nothing has been ingested into the RAG store yet.")
    return ToolResult(content=f"{counts['sources']} source(s), {counts['chunks']} chunk(s) currently ingested.")


def register_rag_tools(registry: ToolRegistry) -> None:
    logger.debug("Entered into register_rag_tools")
    registry.register(ToolDefinition(
        name="rag_search",
        description=(
            "Search previously-ingested project files/documents for relevant content "
            "(hybrid semantic + keyword search over the local RAG store). Use this when "
            "a large file was indexed instead of fully inlined, or after 'elidia rag ingest'."
        ),
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "What to search for"},
            "limit": {"type": "integer", "description": "Max results to return (default 5)"},
        }, "required": ["query"]},
        handler=_rag_search, category="rag",
    ))
    registry.register(ToolDefinition(
        name="rag_list_sources",
        description="Show how many files/chunks are currently ingested into the local RAG store",
        parameters={"type": "object", "properties": {}},
        handler=_rag_list_sources, category="rag",
    ))

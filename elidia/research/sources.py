"""Research data sources — wires MCP servers and built-in tools for research.

Provides a unified search interface that queries multiple sources in parallel:
  - Built-in web search (DuckDuckGo, Tavily)
  - MCP servers (SearXNG, Wikipedia, YouTube, etc.)
  - RAG engine (local documents)

Source categories per plan:
  Academic: arxiv, pubmed, semantic_scholar, wikipedia
  Legal: Legal Data Hunter (needs MCP server — see mcp.json)
  Financial: sec_edgar, fred, yahoo_finance, coingecko (needs API keys)
  News: gnews, newsdata
  Web: duckduckgo, searxng, crawl4ai, web_browser
  Trends: google_trends (needs API key)
  Social: youtube, hackernews
  Policy: think_tank, wayback
  Maps: openstreetmap
  Weather: open_meteo (needs API key)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from elidia.mcp.registry import MCPRegistry
from elidia.research.orchestrator import SearchResult
from elidia.tools.base import ToolRegistry

logger = logging.getLogger(__name__)

MCP_SEARCH_SOURCES: list[tuple[str, str, str]] = [
    # Academic
    ("arxiv", "arxiv_search", "arXiv"),
    ("pubmed", "pubmed_search", "PubMed"),
    ("semantic_scholar", "semantic_scholar_search", "Semantic Scholar"),
    ("wikipedia_research", "wiki_search", "Wikipedia"),
    # Legal (requires Legal Data Hunter MCP server in mcp.json)
    # ("legal_data_hunter", "ldh_search", "Legal Data Hunter"),
    # Financial (requires API keys)
    # ("sec_edgar", "edgar_search", "SEC Edgar"),
    # ("fred", "fred_search", "FRED"),
    # ("yahoo_finance", "yahoo_search", "Yahoo Finance"),
    # ("coingecko", "coingecko_search", "CoinGecko"),
    # News
    ("gnews", "gnews_search", "Google News"),
    ("newsdata", "newsdata_search", "NewsData.io"),
    # Web
    ("searxng", "sx_search", "SearXNG"),
    ("duckduckgo_research", "ddg_search", "DuckDuckGo"),
    ("crawl4ai", "crawl4ai_search", "Crawl4AI"),
    ("web_browser", "web_browser_search", "Web Browser"),
    # Trends (requires API key)
    # ("google_trends", "trends_search", "Google Trends"),
    # Social
    ("youtube_research", "youtube_search", "YouTube"),
    ("hackernews", "hn_search", "Hacker News"),
    # Policy
    ("think_tank", "thinktank_search", "Think Tank"),
    ("wayback", "wayback_search", "Wayback Machine"),
    # Maps
    ("openstreetmap", "osm_search", "OpenStreetMap"),
    # Weather (requires API key)
    # ("open_meteo", "meteo_search", "Open Meteo"),
    # Code
    ("github_research", "github_search", "GitHub"),
]


@dataclass
class SourceConfig:
    name: str
    enabled: bool = True
    max_results: int = 5
    priority: int = 0


class ResearchSources:
    """Unified search across multiple data sources."""

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        mcp_registry: MCPRegistry | None = None,
        rag_search_fn: Any = None,
    ) -> None:
        logger.debug("Entered into ResearchSources.__init__")
        self._tools = tool_registry
        self._mcp = mcp_registry
        self._rag_fn = rag_search_fn
        self._source_configs: dict[str, SourceConfig] = {}

        for server, tool, label in MCP_SEARCH_SOURCES:
            self._source_configs[server] = SourceConfig(name=label)

    async def search(
        self,
        query: str,
        max_results: int = 5,
        sources: list[str] | None = None,
    ) -> list[SearchResult]:
        logger.debug(f"Entered into search: query={query!r}, max_results={max_results}")
        tasks: list[asyncio.Task[list[SearchResult]]] = []

        if self._tools and self._tools.get("web_search"):
            tasks.append(asyncio.ensure_future(
                self._search_builtin(query, max_results)
            ))

        if self._mcp:
            connected = self._mcp.get_connected_servers()
            for server_name, tool_name, label in MCP_SEARCH_SOURCES:
                if sources and server_name not in sources:
                    continue
                cfg = self._source_configs.get(server_name)
                if cfg and not cfg.enabled:
                    continue
                if server_name in connected:
                    tasks.append(asyncio.ensure_future(
                        self._search_mcp(server_name, tool_name, label, query, max_results)
                    ))

        if self._rag_fn:
            tasks.append(asyncio.ensure_future(
                self._search_rag(query, max_results)
            ))

        if not tasks:
            logger.warning("No search sources available")
            return []

        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        combined: list[SearchResult] = []
        seen_urls: set[str] = set()

        for result_set in all_results:
            if isinstance(result_set, Exception):
                logger.warning(f"Search source failed: {result_set}")
                continue
            if isinstance(result_set, list):
                for r in result_set:
                    if r.url and r.url in seen_urls:
                        continue
                    if r.url:
                        seen_urls.add(r.url)
                    combined.append(r)

        logger.info(f"Research search: {len(combined)} results from {len(tasks)} sources")
        return combined

    async def _search_builtin(self, query: str, max_results: int) -> list[SearchResult]:
        logger.debug(f"Entered into _search_builtin: query={query!r}")
        results: list[SearchResult] = []
        try:
            tool_result = await self._tools.call("web_search", {"query": query})
            if not tool_result.is_error:
                for line in tool_result.content.split("\n\n"):
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("\n", 2)
                    title = parts[0] if parts else ""
                    url = ""
                    snippet = ""
                    for p in parts:
                        if p.startswith("http"):
                            url = p.strip()
                        elif p != title:
                            snippet = p.strip()
                    results.append(SearchResult(
                        query=query,
                        title=title,
                        url=url,
                        snippet=snippet,
                        source="DuckDuckGo",
                    ))
        except Exception as e:
            logger.warning(f"Built-in search failed: {e}")
        return results[:max_results]

    async def _search_mcp(
        self,
        server_name: str,
        tool_name: str,
        label: str,
        query: str,
        max_results: int,
    ) -> list[SearchResult]:
        logger.debug(f"Entered into _search_mcp: server={server_name}, query={query!r}")
        results: list[SearchResult] = []
        try:
            mcp_result = await self._mcp.call_tool(
                f"{server_name}__{tool_name}",
                {"query": query, "max_results": max_results},
            )
            if not mcp_result.is_error:
                for item in mcp_result.content:
                    if isinstance(item, dict):
                        text = item.get("text", "")
                    elif isinstance(item, str):
                        text = item
                    else:
                        text = str(item)

                    results.append(SearchResult(
                        query=query,
                        title="",
                        url="",
                        snippet=text[:1000],
                        source=label,
                    ))
        except Exception as e:
            logger.debug(f"MCP source {server_name} failed: {e}")
        return results[:max_results]

    async def _search_rag(self, query: str, max_results: int) -> list[SearchResult]:
        logger.debug(f"Entered into _search_rag: query={query!r}")
        results: list[SearchResult] = []
        try:
            rag_results = await self._rag_fn(query, limit=max_results)
            if isinstance(rag_results, list):
                for r in rag_results:
                    results.append(SearchResult(
                        query=query,
                        title=getattr(r, "source", "local document"),
                        url="",
                        snippet=getattr(r, "content", str(r))[:1000],
                        source="RAG (local)",
                    ))
        except Exception as e:
            logger.warning(f"RAG search failed: {e}")
        return results[:max_results]

    def get_available_sources(self) -> list[dict[str, Any]]:
        logger.debug("Entered into get_available_sources")
        available: list[dict[str, Any]] = []

        if self._tools and self._tools.get("web_search"):
            available.append({"name": "web_search", "type": "builtin", "status": "connected"})

        if self._mcp:
            connected = self._mcp.get_connected_servers()
            for server_name, tool_name, label in MCP_SEARCH_SOURCES:
                status = "connected" if server_name in connected else "disconnected"
                available.append({"name": label, "server": server_name, "type": "mcp", "status": status})

        if self._rag_fn:
            available.append({"name": "RAG (local)", "type": "rag", "status": "connected"})

        return available

    def enable_source(self, name: str) -> None:
        logger.debug(f"Entered into enable_source: name={name}")
        if name in self._source_configs:
            self._source_configs[name].enabled = True

    def disable_source(self, name: str) -> None:
        logger.debug(f"Entered into disable_source: name={name}")
        if name in self._source_configs:
            self._source_configs[name].enabled = False

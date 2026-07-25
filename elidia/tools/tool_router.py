"""Semantic tool router — embeds user messages and finds matching tools.

Unlike the static ModelRouter (which selects a model), this module
selects the best TOOL for a task using semantic search:

1. Embed the user's message with bge-m3 (1024-dim)
2. Cosine-search a sqlite-vec index of tool descriptions + schemas
3. Return top-K tools ranked by relevance

This replaces keyword/regex matching with true semantic understanding.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from elidia.memory.embeddings import EmbeddingClient, EMBEDDING_DIM

logger = logging.getLogger(__name__)

TOP_K_DEFAULT = 8
MIN_SIMILARITY = 0.25


@dataclass
class ToolMatch:
    name: str
    description: str
    category: str
    score: float
    parameters: dict[str, Any] = field(default_factory=dict)


class SemanticToolRouter:
    """Finds the best tools for a user's message using embeddings + vector search."""

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        db_path: str = "",
    ) -> None:
        logger.debug("Entered into SemanticToolRouter.__init__")
        self._embeddings = embedding_client
        self._tools: list[dict[str, Any]] = []
        self._initialized = False
        self._db_path = db_path

    async def index_tools(self, tools: list[dict[str, Any]]) -> None:
        """Embed and index tool descriptions + schemas for semantic search.

        Each tool entry should have: name, description, category, parameters.
        """
        logger.debug(f"Entered into index_tools: count={len(tools)}")
        self._tools = tools

        texts = []
        for tool in tools:
            desc = tool.get("description", "")
            params = tool.get("parameters", {})
            params_str = json.dumps(params, sort_keys=True) if params else ""
            texts.append(f"{desc} {params_str}")

        if not texts:
            return

        self._tool_embeddings = await self._embeddings.embed(texts)
        self._initialized = True
        logger.info(f"Indexed {len(tools)} tools with {len(self._tool_embeddings)} embeddings")

    async def search(
        self,
        query: str,
        top_k: int = TOP_K_DEFAULT,
        min_score: float = MIN_SIMILARITY,
    ) -> list[ToolMatch]:
        """Find the best tools for a user query using cosine similarity."""
        logger.debug(f"Entered into search: query={query[:80]!r}, top_k={top_k}")

        if not self._initialized or not self._tools:
            logger.warning("Tool router not initialized — no tools indexed")
            return []

        query_embedding = await self._embeddings.embed_single(query)

        scores = []
        for i, tool_emb in enumerate(self._tool_embeddings):
            similarity = _cosine_similarity(query_embedding, tool_emb)
            if similarity >= min_score:
                scores.append((i, similarity))

        scores.sort(key=lambda x: x[1], reverse=True)
        top = scores[:top_k]

        matches = []
        for idx, score in top:
            tool = self._tools[idx]
            matches.append(ToolMatch(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                category=tool.get("category", ""),
                score=round(score, 4),
                parameters=tool.get("parameters", {}),
            ))

        return matches

    async def route(
        self,
        query: str,
        tool_registry: Any = None,
        top_k: int = TOP_K_DEFAULT,
    ) -> tuple[ToolMatch | None, list[ToolMatch]]:
        """Route a query to the best tool, with LLM reasoning over candidates.

        Returns (best_match, all_candidates).
        """
        logger.debug(f"Entered into route: query={query[:80]!r}")
        candidates = await self.search(query, top_k=top_k)

        if not candidates:
            return None, []

        best = candidates[0]
        return best, candidates

    def get_indexed_tools(self) -> list[dict[str, Any]]:
        """Return all currently indexed tools."""
        return list(self._tools)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

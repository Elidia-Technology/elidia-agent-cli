import logging
from typing import Any

import httpx

from elidia.rag.engine import RagDocument, SearchResult

logger = logging.getLogger(__name__)


class PortalRagBridge:
    """Queries the user's portal knowledge bases via the Developer API."""

    def __init__(self, api_key: str, base_url: str = "https://developer.aiutils.io/v1") -> None:
        logger.debug(f"Entered into PortalRagBridge.__init__: base_url={base_url}")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def search(
        self,
        query: str,
        collection: str = "",
        limit: int = 10,
    ) -> list[SearchResult]:
        logger.debug(f"Entered into search: query={query!r}, collection={collection}")

        payload: dict[str, Any] = {"query": query, "limit": limit}
        if collection:
            payload["collection"] = collection

        async with httpx.AsyncClient(
            timeout=30,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        ) as client:
            try:
                resp = await client.post(f"{self._base_url}/rag/search", json=payload)
                if resp.status_code == 404:
                    logger.info("Portal RAG endpoint not available")
                    return []
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                logger.warning(f"Portal RAG search failed: {e}")
                return []
            except Exception as e:
                logger.warning(f"Portal RAG error: {e}")
                return []

        results: list[SearchResult] = []
        for item in data.get("results", data.get("data", [])):
            doc = RagDocument(
                id=item.get("id", ""),
                source=item.get("source", item.get("file_name", "")),
                content=item.get("content", item.get("text", "")),
                content_type="portal",
                metadata=item.get("metadata", {}),
            )
            score = item.get("score", item.get("similarity", 0.0))
            results.append(SearchResult(document=doc, score=score, match_type="portal"))

        return results

    async def list_collections(self) -> list[dict[str, Any]]:
        logger.debug("Entered into list_collections")
        async with httpx.AsyncClient(
            timeout=15,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        ) as client:
            try:
                resp = await client.get(f"{self._base_url}/rag/collections")
                if resp.status_code == 404:
                    return []
                resp.raise_for_status()
                data = resp.json()
                return data.get("collections", data.get("data", []))
            except Exception as e:
                logger.warning(f"Failed to list portal collections: {e}")
                return []

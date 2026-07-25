import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "bge-m3"


class EmbeddingClient:
    """Generates embeddings via the AiUtils Developer API."""

    def __init__(self, api_key: str, base_url: str = "https://developer.aiutils.io/v1") -> None:
        logger.debug(f"Entered into EmbeddingClient.__init__: base_url={base_url}")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def embed(self, texts: list[str], model: str = DEFAULT_MODEL) -> list[list[float]]:
        logger.debug(f"Entered into embed: count={len(texts)}, model={model}")
        if not texts:
            return []

        async with httpx.AsyncClient(
            timeout=60,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        ) as client:
            resp = await client.post(
                f"{self._base_url}/embeddings",
                json={"input": texts, "model": model},
            )
            resp.raise_for_status()
            data = resp.json()

        embeddings_data: list[dict[str, Any]] = data.get("data", [])
        embeddings_data.sort(key=lambda e: e.get("index", 0))
        return [e["embedding"] for e in embeddings_data]

    async def embed_single(self, text: str, model: str = DEFAULT_MODEL) -> list[float]:
        logger.debug(f"Entered into embed_single: len={len(text)}")
        results = await self.embed([text], model=model)
        if not results:
            raise RuntimeError("Embedding API returned no results")
        return results[0]

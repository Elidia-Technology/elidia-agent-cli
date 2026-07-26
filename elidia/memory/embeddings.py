"""Embedding generation — remote API (primary) + local ONNX (offline fallback).

Uses AiUtils Developer API by default. The Developer Platform's public
/v1/embeddings only proxies real vendor models (OpenAI etc) — it does not
serve the portal's internal bge-m3 model, so we request
text-embedding-3-small with dimensions=1024 (Matryoshka truncation) to
match bge-m3's native 1024-dim output that the rest of this codebase's
local memory/RAG schema (sqlite-vec) is built around. Verified 2026-07-26:
"bge-m3" as a model id 503s — it isn't a real model on this endpoint.
Falls back to local ONNX Runtime when the model is available at
~/.elidia/models/bge-m3/ and the API is unreachable or balance is zero.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1024
LOCAL_MODEL_DIR = Path.home() / ".elidia" / "models" / "bge-m3"


class EmbeddingClient:
    """Generates embeddings via the AiUtils Developer API.

    Falls back to local ONNX inference when the model is downloaded
    and the API is unreachable.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://developer.aiutils.io/v1",
        prefer_local: bool = False,
    ) -> None:
        logger.debug(f"Entered into EmbeddingClient.__init__: base_url={base_url}")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._prefer_local = prefer_local
        self._local: LocalEmbedder | None = None

    async def embed(self, texts: list[str], model: str = DEFAULT_MODEL) -> list[list[float]]:
        logger.debug(f"Entered into embed: count={len(texts)}, model={model}")
        if not texts:
            return []

        # Try local first if preferred and model is available
        if self._prefer_local:
            result = await self._embed_local(texts)
            if result is not None:
                return result

        # Primary: remote API
        try:
            return await self._embed_api(texts, model)
        except Exception as e:
            logger.warning(f"API embedding failed, trying local: {e}")
            result = await self._embed_local(texts)
            if result is not None:
                return result
            raise RuntimeError(f"Embedding failed — API error and no local model available: {e}") from e

    async def embed_single(self, text: str, model: str = DEFAULT_MODEL) -> list[float]:
        logger.debug(f"Entered into embed_single: len={len(text)}")
        results = await self.embed([text], model=model)
        if not results:
            raise RuntimeError("Embedding returned no results")
        return results[0]

    # --- internal ---

    async def _embed_api(self, texts: list[str], model: str) -> list[list[float]]:
        async with httpx.AsyncClient(
            timeout=60,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        ) as client:
            resp = await client.post(
                f"{self._base_url}/embeddings",
                json={"input": texts, "model": model, "dimensions": EMBEDDING_DIM},
            )
            resp.raise_for_status()
            data = resp.json()

        embeddings_data: list[dict[str, Any]] = data.get("data", [])
        embeddings_data.sort(key=lambda e: e.get("index", 0))
        return [e["embedding"] for e in embeddings_data]

    async def _embed_local(self, texts: list[str]) -> list[list[float]] | None:
        """Try local ONNX embedding. Returns None if model not available."""
        if self._local is None:
            if not _local_model_available():
                return None
            try:
                self._local = LocalEmbedder()
            except Exception as e:
                logger.warning(f"Failed to initialize local embedder: {e}")
                return None
        try:
            return self._local.embed(texts)
        except Exception as e:
            logger.warning(f"Local embedding failed: {e}")
            return None


class LocalEmbedder:
    """Local embedding inference using ONNX Runtime + bge-m3.

    Requires the ONNX model at ~/.elidia/models/bge-m3/.
    Download with: pip install optimum[onnxruntime]
      python -c "from optimum.onnxruntime import ORTModelForFeatureExtraction;
      model = ORTModelForFeatureExtraction.from_pretrained('BAAI/bge-m3', export=True);
      model.save_pretrained('~/.elidia/models/bge-m3/')"
    """

    def __init__(self, model_dir: Path | None = None) -> None:
        logger.debug("Entered into LocalEmbedder.__init__")
        import numpy as np
        import onnxruntime as ort

        self._np = np
        self._model_dir = model_dir or LOCAL_MODEL_DIR
        model_path = self._model_dir / "model.onnx"

        if not model_path.exists():
            raise FileNotFoundError(f"ONNX model not found at {model_path}")

        self._session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )

        # Load tokenizer if available
        self._tokenizer = None
        tokenizer_path = self._model_dir / "tokenizer.json"
        if tokenizer_path.exists():
            try:
                from transformers import AutoTokenizer
                self._tokenizer = AutoTokenizer.from_pretrained(str(self._model_dir))
            except ImportError:
                logger.debug("transformers not installed — using basic whitespace tokenization")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts."""
        logger.debug(f"Entered into LocalEmbedder.embed: count={len(texts)}")

        if self._tokenizer:
            inputs = self._tokenizer(
                texts, padding=True, truncation=True, max_length=512,
                return_tensors="np",
            )
            onnx_inputs = {
                "input_ids": inputs["input_ids"],
                "attention_mask": inputs["attention_mask"],
            }
        else:
            # Fallback: basic whitespace tokenization (limited accuracy)
            onnx_inputs = self._basic_tokenize(texts)

        outputs = self._session.run(None, onnx_inputs)
        embeddings = outputs[0]

        # Mean pooling if output is token-level
        if len(embeddings.shape) == 3:
            if self._tokenizer and "attention_mask" in onnx_inputs:
                mask = self._np.expand_dims(onnx_inputs["attention_mask"], -1)
                embeddings = (embeddings * mask).sum(axis=1) / mask.sum(axis=1)
            else:
                embeddings = embeddings.mean(axis=1)

        # Normalize
        norms = self._np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = self._np.where(norms == 0, 1.0, norms)
        embeddings = embeddings / norms

        return embeddings.tolist()

    def _basic_tokenize(self, texts: list[str]) -> dict[str, Any]:
        """Minimal tokenization fallback when transformers unavailable."""
        max_len = max(len(t.split()) for t in texts)
        max_len = min(max(max_len, 1), 512)
        input_ids = self._np.zeros((len(texts), max_len), dtype=self._np.int64)
        attention_mask = self._np.zeros((len(texts), max_len), dtype=self._np.int64)
        for i, text in enumerate(texts):
            tokens = text.lower().split()[:max_len]
            for j, token in enumerate(tokens):
                input_ids[i, j] = hash(token) % 30000 + 1  # approximate vocab mapping
            attention_mask[i, :len(tokens)] = 1
        return {"input_ids": input_ids, "attention_mask": attention_mask}


def _local_model_available() -> bool:
    """Check if the local ONNX model is downloaded."""
    return (LOCAL_MODEL_DIR / "model.onnx").exists()

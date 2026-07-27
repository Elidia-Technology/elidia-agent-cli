"""Local model provider — Ollama client for fully-offline chat.

Talks to a local Ollama instance at http://localhost:11434. No API key
needed, no network beyond localhost — this is the "local/offline chat
model" competitive gap item (AIUT-2153, item 2).

Ollama's /api/chat endpoint is OpenAI-compatible enough that we can
use the same ChatMessage format the rest of the codebase already passes
around, with minimal translation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from elidia.api.client import ChatMessage

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://localhost:11434"


@dataclass
class LocalModelInfo:
    name: str
    size_bytes: int
    parameter_size: str
    context_length: int
    capabilities: list[str]


async def list_local_models() -> list[LocalModelInfo]:
    """Return models currently available in the local Ollama instance."""
    logger.debug("Entered into list_local_models")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OLLAMA_BASE}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Ollama not reachable: {e}")
        return []

    models: list[LocalModelInfo] = []
    for m in data.get("models", []):
        details = m.get("details", {})
        models.append(LocalModelInfo(
            name=m["name"],
            size_bytes=m.get("size", 0),
            parameter_size=details.get("parameter_size", "?"),
            context_length=details.get("context_length", 4096),
            capabilities=m.get("capabilities", []),
        ))
    return models


async def chat_local(
    messages: list[ChatMessage],
    model: str = "qwen3:1.7b",
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> str:
    """Send a chat completion to a local Ollama model. Returns the
    assistant's text content (non-streaming for simplicity — the daemon
    IPC is already streaming at a higher level)."""
    logger.debug(f"Entered into chat_local: model={model}, msgs={len(messages)}")

    ollama_msgs = []
    for m in messages:
        content = m.content
        if isinstance(content, list):
            # Multimodal — Ollama supports images as base64 in content
            # blocks. For now, extract text only; vision is a follow-up.
            text_parts = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
            content = " ".join(text_parts)
        ollama_msgs.append({"role": m.role, "content": content})

    payload: dict = {
        "model": model,
        "messages": ollama_msgs,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if max_tokens:
        payload["options"]["num_predict"] = max_tokens

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    return data.get("message", {}).get("content", "")

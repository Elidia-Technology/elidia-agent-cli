"""LRU response cache — caches LLM and tool responses within a session.

Provides a bounded cache with TTL support for deduplicating identical
queries within a session. The cache key is derived from the model,
message content, and temperature to avoid cross-contamination.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    hits: int = 0
    ttl: float = 0.0

    @property
    def is_expired(self) -> bool:
        if self.ttl <= 0:
            return False
        return (time.time() - self.created_at) > self.ttl


class ResponseCache:
    """Bounded LRU cache with TTL for LLM responses."""

    def __init__(
        self,
        max_size: int = 128,
        default_ttl: float = 300.0,
        enabled: bool = True,
    ) -> None:
        logger.debug(f"Entered into ResponseCache.__init__: max_size={max_size}, ttl={default_ttl}")
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._enabled = enabled
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._total_hits: int = 0
        self._total_misses: int = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def make_key(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        logger.debug(f"Entered into make_key: model={model}")
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def get(self, key: str) -> Any | None:
        logger.debug(f"Entered into get: key={key[:12]}")
        if not self._enabled:
            return None

        entry = self._cache.get(key)
        if entry is None:
            self._total_misses += 1
            return None

        if entry.is_expired:
            del self._cache[key]
            self._total_misses += 1
            return None

        entry.hits += 1
        self._total_hits += 1
        self._cache.move_to_end(key)
        return entry.value

    def put(
        self,
        key: str,
        value: Any,
        ttl: float | None = None,
    ) -> None:
        logger.debug(f"Entered into put: key={key[:12]}")
        if not self._enabled:
            return

        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key].value = value
            self._cache[key].created_at = time.time()
            return

        while len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)

        self._cache[key] = CacheEntry(
            key=key,
            value=value,
            ttl=ttl if ttl is not None else self._default_ttl,
        )

    def invalidate(self, key: str) -> bool:
        logger.debug(f"Entered into invalidate: key={key[:12]}")
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> int:
        logger.debug("Entered into clear")
        count = len(self._cache)
        self._cache.clear()
        return count

    def evict_expired(self) -> int:
        logger.debug("Entered into evict_expired")
        expired_keys = [k for k, v in self._cache.items() if v.is_expired]
        for k in expired_keys:
            del self._cache[k]
        return len(expired_keys)

    def get_stats(self) -> dict[str, Any]:
        logger.debug("Entered into get_stats")
        total = self._total_hits + self._total_misses
        hit_rate = (self._total_hits / total * 100) if total > 0 else 0.0
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._total_hits,
            "misses": self._total_misses,
            "hit_rate_pct": round(hit_rate, 1),
            "default_ttl": self._default_ttl,
            "enabled": self._enabled,
        }

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        entry = self._cache.get(key)
        return entry is not None and not entry.is_expired

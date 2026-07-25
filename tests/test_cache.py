"""Tests for elidia.cache.lru — LRU response cache."""
import time

import pytest

from elidia.cache.lru import CacheEntry, ResponseCache


class TestCacheEntry:
    def test_not_expired_within_ttl(self):
        entry = CacheEntry(key="k", value="v", ttl=60.0)
        assert not entry.is_expired

    def test_expired_past_ttl(self):
        entry = CacheEntry(key="k", value="v", created_at=time.time() - 100, ttl=10.0)
        assert entry.is_expired

    def test_no_ttl_never_expires(self):
        entry = CacheEntry(key="k", value="v", ttl=0.0)
        assert not entry.is_expired

    def test_hits_counter(self):
        entry = CacheEntry(key="k", value="v")
        assert entry.hits == 0
        entry.hits += 1
        assert entry.hits == 1


class TestResponseCache:
    def test_basic_put_get(self):
        cache = ResponseCache(max_size=10)
        cache.put("k1", "v1")
        assert cache.get("k1") == "v1"

    def test_miss_returns_none(self):
        cache = ResponseCache(max_size=10)
        assert cache.get("missing") is None

    def test_lru_eviction(self):
        cache = ResponseCache(max_size=3, default_ttl=300)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        assert len(cache) == 3

        cache.put("d", 4)
        assert len(cache) == 3
        assert cache.get("a") is None  # evicted (oldest)
        assert cache.get("d") == 4

    def test_lru_access_refreshes(self):
        cache = ResponseCache(max_size=3, default_ttl=300)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)

        cache.get("a")  # refresh 'a' — now 'b' is oldest
        cache.put("d", 4)
        assert cache.get("b") is None  # evicted
        assert cache.get("a") == 1     # still alive

    def test_invalidate(self):
        cache = ResponseCache()
        cache.put("k", "v")
        assert cache.invalidate("k")
        assert cache.get("k") is None
        assert not cache.invalidate("nonexistent")

    def test_clear(self):
        cache = ResponseCache()
        cache.put("a", 1)
        cache.put("b", 2)
        count = cache.clear()
        assert count == 2
        assert len(cache) == 0

    def test_contains(self):
        cache = ResponseCache()
        cache.put("k", "v")
        assert "k" in cache
        assert "missing" not in cache

    def test_expired_entry_not_returned(self):
        cache = ResponseCache(default_ttl=0.01)
        cache.put("k", "v")
        time.sleep(0.02)
        assert cache.get("k") is None

    def test_evict_expired(self):
        cache = ResponseCache(default_ttl=0.01)
        cache.put("a", 1)
        cache.put("b", 2)
        time.sleep(0.02)
        count = cache.evict_expired()
        assert count == 2
        assert len(cache) == 0

    def test_make_key_deterministic(self):
        cache = ResponseCache()
        msgs = [{"role": "user", "content": "hello"}]
        k1 = cache.make_key("model-a", msgs, 0.7)
        k2 = cache.make_key("model-a", msgs, 0.7)
        assert k1 == k2
        assert len(k1) == 32

    def test_make_key_varies_by_model(self):
        cache = ResponseCache()
        msgs = [{"role": "user", "content": "hello"}]
        k1 = cache.make_key("model-a", msgs, 0.7)
        k2 = cache.make_key("model-b", msgs, 0.7)
        assert k1 != k2

    def test_make_key_varies_by_temperature(self):
        cache = ResponseCache()
        msgs = [{"role": "user", "content": "hello"}]
        k1 = cache.make_key("model-a", msgs, 0.3)
        k2 = cache.make_key("model-a", msgs, 0.9)
        assert k1 != k2

    def test_disabled_cache(self):
        cache = ResponseCache(enabled=False)
        cache.put("k", "v")
        assert cache.get("k") is None
        assert len(cache) == 0

    def test_enable_disable(self):
        cache = ResponseCache()
        cache.put("k", "v")
        assert cache.get("k") == "v"
        cache.enabled = False
        assert cache.get("k") is None
        cache.enabled = True
        assert cache.get("k") == "v"

    def test_stats(self):
        cache = ResponseCache(max_size=10)
        cache.put("k1", "v1")
        cache.get("k1")  # hit
        cache.get("k2")  # miss

        stats = cache.get_stats()
        assert stats["size"] == 1
        assert stats["max_size"] == 10
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate_pct"] == 50.0

    def test_update_existing_key(self):
        cache = ResponseCache()
        cache.put("k", "v1")
        cache.put("k", "v2")
        assert cache.get("k") == "v2"
        assert len(cache) == 1

    def test_custom_ttl_per_entry(self):
        cache = ResponseCache(default_ttl=300)
        cache.put("short", "v", ttl=0.01)
        cache.put("long", "v", ttl=300)
        time.sleep(0.02)
        assert cache.get("short") is None
        assert cache.get("long") == "v"

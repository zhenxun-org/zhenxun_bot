from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import sys
import time
from typing import Generic, TypeVar
import weakref

K = TypeVar("K")
V = TypeVar("V")


def _default_sizeof(value: object) -> int:
    if isinstance(value, bytes | bytearray | memoryview):
        return len(value)
    return 0


@dataclass(frozen=True)
class BoundedTTLCacheStats:
    name: str
    items: int
    max_items: int
    total_bytes: int
    max_total_bytes: int | None
    hits: int
    misses: int
    sets: int
    evictions: int

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "name": self.name,
            "items": self.items,
            "max_items": self.max_items,
            "total_bytes": self.total_bytes,
            "max_total_bytes": self.max_total_bytes,
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "evictions": self.evictions,
        }


class BoundedTTLCache(Generic[K, V]):
    """Small async TTL/LRU cache with optional total-byte limit."""

    _instances: weakref.WeakSet["BoundedTTLCache"] = weakref.WeakSet()

    def __init__(
        self,
        name: str,
        ttl_seconds: float,
        max_items: int,
        max_total_bytes: int | None = None,
        sizeof: Callable[[V], int] | None = None,
    ) -> None:
        self.name = name.upper()
        self._ttl_seconds = max(ttl_seconds, 0.0)
        self._max_items = max(max_items, 1)
        self._max_total_bytes = (
            max_total_bytes
            if isinstance(max_total_bytes, int) and max_total_bytes > 0
            else None
        )
        self._sizeof = sizeof or _default_sizeof
        self._cache: OrderedDict[K, tuple[float, V, int]] = OrderedDict()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._evictions = 0
        self._lock = asyncio.Lock()
        self._last_sweep = 0.0
        self.__class__._instances.add(self)

    # 全量过期清理的最小间隔(B1):避免每次 get 都 O(N) 扫描整表。
    _SWEEP_INTERVAL = 30.0

    def _expire_at(self, now: float) -> float:
        if self._ttl_seconds <= 0:
            return sys.float_info.max
        return now + self._ttl_seconds

    def _value_size(self, value: V) -> int:
        try:
            return max(0, int(self._sizeof(value)))
        except Exception:
            return 0

    def _remove_key_nolock(self, key: K) -> bool:
        item = self._cache.pop(key, None)
        if item is None:
            return False
        self._total_bytes -= item[2]
        if self._total_bytes < 0:
            self._total_bytes = 0
        return True

    def _pop_oldest_nolock(self) -> bool:
        if not self._cache:
            return False
        _, (_, _, size) = self._cache.popitem(last=False)
        self._total_bytes -= size
        if self._total_bytes < 0:
            self._total_bytes = 0
        self._evictions += 1
        return True

    def _enforce_capacity_nolock(self) -> None:
        """容量边界强制(O(溢出量)):仅在新增后调用,不扫描全表。"""
        while len(self._cache) > self._max_items:
            self._pop_oldest_nolock()
        if self._max_total_bytes is not None:
            while self._total_bytes > self._max_total_bytes and self._cache:
                self._pop_oldest_nolock()

    def _sweep_expired_nolock(self, now: float) -> None:
        """全量过期清理(O(N)):由节流器或后台/governor 低频触发。"""
        self._last_sweep = now
        expired_keys = [
            key for key, (expire_at, _, _) in self._cache.items() if expire_at <= now
        ]
        for key in expired_keys:
            if self._remove_key_nolock(key):
                self._evictions += 1

    def _maybe_sweep_nolock(self, now: float) -> None:
        if now - self._last_sweep >= self._SWEEP_INTERVAL:
            self._sweep_expired_nolock(now)

    def _cleanup_nolock(self, now: float) -> None:
        """完整清理(全量过期 + 容量):保留给 stats / 显式调用。"""
        self._sweep_expired_nolock(now)
        self._enforce_capacity_nolock()

    async def get(self, key: K) -> V | None:
        now = time.monotonic()
        async with self._lock:
            # 仅做命中项的单条过期检查(O(1)),全量清理改为低频节流(B1)。
            item = self._cache.get(key)
            if item is None:
                self._misses += 1
                return None
            expire_at, value, _ = item
            if expire_at <= now:
                self._remove_key_nolock(key)
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    async def set(self, key: K, value: V) -> bool:
        value_size = self._value_size(value)
        if self._max_total_bytes is not None and value_size > self._max_total_bytes:
            return False

        now = time.monotonic()
        async with self._lock:
            self._remove_key_nolock(key)
            self._cache[key] = (self._expire_at(now), value, value_size)
            self._total_bytes += value_size
            self._sets += 1
            self._cache.move_to_end(key)
            # 新增后必做容量强制(廉价);全量过期清理走低频节流(B1)。
            self._maybe_sweep_nolock(now)
            self._enforce_capacity_nolock()
            return key in self._cache

    async def delete(self, key: K) -> bool:
        async with self._lock:
            return self._remove_key_nolock(key)

    async def clear(self) -> int:
        async with self._lock:
            size = len(self._cache)
            self._cache.clear()
            self._total_bytes = 0
            return size

    async def stats(self) -> BoundedTTLCacheStats:
        now = time.monotonic()
        async with self._lock:
            self._cleanup_nolock(now)
            return BoundedTTLCacheStats(
                name=self.name,
                items=len(self._cache),
                max_items=self._max_items,
                total_bytes=self._total_bytes,
                max_total_bytes=self._max_total_bytes,
                hits=self._hits,
                misses=self._misses,
                sets=self._sets,
                evictions=self._evictions,
            )

    @classmethod
    async def clear_all(cls) -> dict[str, int]:
        result: dict[str, int] = {}
        for cache in list(cls._instances):
            size = await cache.clear()
            if size:
                result[cache.name] = result.get(cache.name, 0) + size
        return result

    @classmethod
    async def stats_all(cls) -> dict[str, dict[str, int | str | None]]:
        result: dict[str, dict[str, int | str | None]] = {}
        for cache in list(cls._instances):
            stats = await cache.stats()
            if not stats.items:
                continue
            if cache.name not in result:
                result[cache.name] = stats.to_dict()
                continue
            current = result[cache.name]
            for key in (
                "items",
                "max_items",
                "total_bytes",
                "hits",
                "misses",
                "sets",
                "evictions",
            ):
                current[key] = int(current.get(key) or 0) + int(
                    getattr(stats, key) or 0
                )
            current_max_bytes = current.get("max_total_bytes")
            if current_max_bytes is not None or stats.max_total_bytes is not None:
                current["max_total_bytes"] = int(current_max_bytes or 0) + int(
                    stats.max_total_bytes or 0
                )
        return result

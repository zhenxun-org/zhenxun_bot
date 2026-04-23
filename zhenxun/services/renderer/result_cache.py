from __future__ import annotations

import hashlib
from typing import Any

from zhenxun.services.cache.bounded_ttl import BoundedTTLCache
from zhenxun.utils.pydantic_compat import dump_json_safely


class RenderResultMemoryCache:
    def __init__(
        self,
        ttl_seconds: float,
        max_items: int,
        max_total_bytes: int | None = None,
    ):
        self._ttl_seconds = max(ttl_seconds, 0.0)
        self._max_items = max(max_items, 1)
        self._max_total_bytes = (
            max_total_bytes
            if isinstance(max_total_bytes, int) and max_total_bytes > 0
            else None
        )
        self._cache = BoundedTTLCache[str, bytes](
            "RENDER_RESULT",
            ttl_seconds=self._ttl_seconds,
            max_items=self._max_items,
            max_total_bytes=self._max_total_bytes,
        )

    @staticmethod
    def build_key(payload: Any) -> str:
        payload_text = dump_json_safely(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()

    async def get(self, key: str) -> bytes | None:
        return await self._cache.get(key)

    async def set(self, key: str, value: bytes) -> None:
        await self._cache.set(key, value)

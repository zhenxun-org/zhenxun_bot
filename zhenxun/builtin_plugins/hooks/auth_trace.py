from __future__ import annotations

import time

from .auth.config import WARNING_THRESHOLD


class HookTraceRecorder:
    def __init__(self, start_time: float) -> None:
        self._start_time = start_time
        self._enabled = False
        self._data: dict[str, str] = {}

    def _ensure_enabled(self) -> bool:
        if self._enabled:
            return True
        if time.time() - self._start_time <= WARNING_THRESHOLD:
            return False
        self._enabled = True
        return True

    def set(self, key: str, value: str) -> None:
        if self._ensure_enabled():
            self._data[key] = value

    def setdefault(self, key: str, value: str) -> None:
        if self._ensure_enabled():
            self._data.setdefault(key, value)

    def contains(self, key: str) -> bool:
        return key in self._data

    def snapshot(self) -> dict[str, str]:
        return self._data if self._enabled else {}


__all__ = ["HookTraceRecorder"]

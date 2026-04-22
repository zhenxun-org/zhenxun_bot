from __future__ import annotations

import time

_OVERLOAD_UNTIL = 0.0
_LAST_ACTIVITY = time.monotonic()


def mark_activity() -> None:
    """Record lightweight runtime activity for idle-only maintenance jobs."""
    global _LAST_ACTIVITY
    _LAST_ACTIVITY = time.monotonic()


def idle_seconds() -> float:
    return max(0.0, time.monotonic() - _LAST_ACTIVITY)


def signal_overload(duration: float = 5.0) -> None:
    """Mark the system as overloaded for a short time window."""
    global _OVERLOAD_UNTIL
    if duration <= 0:
        return
    now = time.monotonic()
    until = now + duration
    if until > _OVERLOAD_UNTIL:
        _OVERLOAD_UNTIL = until


def is_overloaded() -> bool:
    return time.monotonic() < _OVERLOAD_UNTIL


def should_pause_tasks() -> bool:
    return is_overloaded()

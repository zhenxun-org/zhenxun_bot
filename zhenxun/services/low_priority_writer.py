from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time
from typing import Any

from zhenxun.services.log import logger
from zhenxun.services.message_load import should_pause_db_tasks, signal_db_unhealthy
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

LOG_COMMAND = "LowPriorityWriter"

WriteBatch = Callable[[list[Any], str], Awaitable[None]]

_POLL_INTERVAL_SECONDS = 1.0
_DB_UNHEALTHY_SECONDS = 30.0


@dataclass(slots=True)
class LowPriorityWriterConfig:
    name: str
    write_batch: WriteBatch
    batch_size: int = 500
    trigger_size: int = 500
    max_retain: int = 10_000
    flush_interval_seconds: float = 60.0
    max_items_per_cycle: int = 1_000
    backoff_base_seconds: float = 30.0
    backoff_max_seconds: float = 600.0
    log_command: str = LOG_COMMAND


@dataclass(slots=True)
class _WriterState:
    config: LowPriorityWriterConfig
    buffer: deque[Any] = field(default_factory=deque)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    dropped: int = 0
    last_drop_log_at: float = 0.0
    last_flush_at: float = field(default_factory=time.monotonic)
    failures: int = 0
    backoff_until: float = 0.0


_WRITERS: dict[str, _WriterState] = {}
_WORKER_TASK: asyncio.Task[None] | None = None
_WAKE_EVENT: asyncio.Event | None = None
_FLUSH_LOCK = asyncio.Lock()
_ACTIVE_FLUSHES = 0
_STOPPING = False


def _wake() -> None:
    if _WAKE_EVENT is not None:
        _WAKE_EVENT.set()


def _ensure_worker() -> None:
    global _STOPPING, _WAKE_EVENT, _WORKER_TASK
    if _STOPPING:
        _STOPPING = False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _WAKE_EVENT is None:
        _WAKE_EVENT = asyncio.Event()
    if _WORKER_TASK is not None and not _WORKER_TASK.done():
        return
    _WORKER_TASK = loop.create_task(_worker_loop())


def register_low_priority_writer(config: LowPriorityWriterConfig) -> None:
    """Register or update an append-only low-priority DB writer."""
    if config.batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if config.trigger_size <= 0:
        raise ValueError("trigger_size must be positive")
    if config.max_retain <= 0:
        raise ValueError("max_retain must be positive")
    state = _WRITERS.get(config.name)
    if state is None:
        _WRITERS[config.name] = _WriterState(config=config)
    else:
        state.config = config
    _ensure_worker()


async def append_low_priority_record(name: str, record: Any) -> bool:
    """Append a record without doing DB work in the caller's hot path."""
    state = _WRITERS.get(name)
    if state is None:
        raise KeyError(f"low priority writer not registered: {name}")
    _ensure_worker()
    should_wake = False
    async with state.lock:
        if len(state.buffer) >= state.config.max_retain:
            state.buffer.popleft()
            state.dropped += 1
            _log_drop_if_needed(state)
        state.buffer.append(record)
        should_wake = len(state.buffer) >= state.config.trigger_size
    if should_wake:
        _wake()
    return True


def _log_drop_if_needed(state: _WriterState) -> None:
    now = time.monotonic()
    if now - state.last_drop_log_at < 10.0:
        return
    state.last_drop_log_at = now
    logger.warning(
        f"{state.config.name} low priority buffer full, "
        f"dropped={state.dropped}, backlog={len(state.buffer)}",
        state.config.log_command,
    )


async def flush_low_priority_writer(
    name: str,
    reason: str,
    *,
    force: bool = False,
) -> int:
    state = _WRITERS.get(name)
    if state is None:
        return 0
    async with _FLUSH_LOCK:
        return await _flush_state(state, reason, force=force)


async def flush_all_low_priority_writers(
    reason: str,
    *,
    force: bool = False,
) -> int:
    total = 0
    async with _FLUSH_LOCK:
        for state in list(_WRITERS.values()):
            total += await _flush_state(state, reason, force=force)
    return total


async def _worker_loop() -> None:
    while not _STOPPING:
        event = _WAKE_EVENT
        if event is None:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        else:
            try:
                await asyncio.wait_for(
                    event.wait(),
                    timeout=_POLL_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                pass
            event.clear()
        if should_pause_db_tasks():
            continue
        async with _FLUSH_LOCK:
            for state in list(_WRITERS.values()):
                if _state_due_for_flush(state):
                    await _flush_state(state, "低优先队列")


def _state_due_for_flush(state: _WriterState) -> bool:
    if not state.buffer:
        return False
    now = time.monotonic()
    if now < state.backoff_until:
        return False
    return (
        len(state.buffer) >= state.config.trigger_size
        or now - state.last_flush_at >= state.config.flush_interval_seconds
    )


async def _flush_state(
    state: _WriterState,
    reason: str,
    *,
    force: bool = False,
) -> int:
    if not force:
        if should_pause_db_tasks():
            return 0
        if time.monotonic() < state.backoff_until:
            return 0
    written = 0
    max_items = state.config.max_items_per_cycle if not force else float("inf")
    while written < max_items:
        batch = await _take_batch(state)
        if not batch:
            break
        try:
            await _write_batch(state, batch, reason)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            # asyncio.wait_for timeout only cancels the awaiter. With SQLite/aiosqlite
            # the worker thread may still finish the SQL later, so restoring this
            # append-only low priority batch can duplicate rows. Prefer dropping the
            # uncertain batch; chat history/statistics/logs are lossy by design here.
            _mark_uncertain_timeout(state, reason, exc, len(batch))
            break
        except Exception as exc:
            await _restore_batch(state, batch)
            _mark_failure(state, reason, exc)
            break
        written += len(batch)
        state.failures = 0
        state.backoff_until = 0.0
        state.last_flush_at = time.monotonic()
    if written:
        logger.debug(
            f"{reason}写入 {state.config.name} {written} 条, "
            f"backlog={len(state.buffer)}",
            state.config.log_command,
        )
    return written


async def _take_batch(state: _WriterState) -> list[Any]:
    batch: list[Any] = []
    async with state.lock:
        while state.buffer and len(batch) < state.config.batch_size:
            batch.append(state.buffer.popleft())
    return batch


async def _restore_batch(state: _WriterState, batch: list[Any]) -> None:
    if not batch:
        return
    async with state.lock:
        retain_count = max(state.config.max_retain - len(state.buffer), 0)
        restore_items = batch[-retain_count:] if retain_count else []
        for record in reversed(restore_items):
            state.buffer.appendleft(record)
        dropped = len(batch) - len(restore_items)
        if dropped:
            state.dropped += dropped
            _log_drop_if_needed(state)


async def _write_batch(
    state: _WriterState,
    batch: list[Any],
    reason: str,
) -> None:
    global _ACTIVE_FLUSHES
    _ACTIVE_FLUSHES += 1
    try:
        await state.config.write_batch(batch, reason)
    finally:
        _ACTIVE_FLUSHES = max(_ACTIVE_FLUSHES - 1, 0)


def _mark_failure(state: _WriterState, reason: str, exc: Exception) -> None:
    state.failures += 1
    backoff = min(
        state.config.backoff_base_seconds * (2 ** (state.failures - 1)),
        state.config.backoff_max_seconds,
    )
    state.backoff_until = time.monotonic() + backoff
    signal_db_unhealthy(_DB_UNHEALTHY_SECONDS, reason=f"{state.config.name}:{reason}")
    logger.warning(
        f"{reason}写入 {state.config.name} 失败, "
        f"backoff={backoff:.0f}s, backlog={len(state.buffer)}",
        state.config.log_command,
        e=exc,
    )


def _mark_uncertain_timeout(
    state: _WriterState,
    reason: str,
    exc: BaseException,
    batch_size: int,
) -> None:
    state.failures += 1
    backoff = min(
        state.config.backoff_base_seconds * (2 ** (state.failures - 1)),
        state.config.backoff_max_seconds,
    )
    state.backoff_until = time.monotonic() + backoff
    state.dropped += batch_size
    signal_db_unhealthy(_DB_UNHEALTHY_SECONDS, reason=f"{state.config.name}:{reason}")
    log_exc = exc if isinstance(exc, Exception) else None
    logger.warning(
        f"{reason}写入 {state.config.name} 超时, "
        f"dropped_uncertain={batch_size}, backoff={backoff:.0f}s, "
        f"backlog={len(state.buffer)}",
        state.config.log_command,
        e=log_exc,
    )


def low_priority_writer_active_count() -> int:
    return _ACTIVE_FLUSHES


def low_priority_writer_backlog() -> dict[str, int]:
    return {name: len(state.buffer) for name, state in _WRITERS.items()}


async def stop_low_priority_writer() -> int:
    global _STOPPING, _WORKER_TASK
    _STOPPING = True
    task = _WORKER_TASK
    _WORKER_TASK = None
    if task is not None:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    return await flush_all_low_priority_writers("关闭", force=True)


@PriorityLifecycle.on_startup(priority=3)
async def _start_low_priority_writer() -> None:
    _ensure_worker()


@PriorityLifecycle.on_shutdown(priority=95)
async def _stop_low_priority_writer() -> None:
    await stop_low_priority_writer()

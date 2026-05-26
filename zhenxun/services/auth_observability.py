from __future__ import annotations

import asyncio
from collections import deque
import contextlib
from dataclasses import dataclass
import random
import time
from typing import TypeVar

from zhenxun.builtin_plugins.hooks.auth_runtime_config import (
    AUTH_OBSERVABILITY_RUNTIME_CONFIG,
)
from zhenxun.models.auth_decision_log import AuthDecisionLog
from zhenxun.models.runtime_backpressure_log import RuntimeBackpressureLog
from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

LOG_COMMAND = "AuthObservability"

_BUFFER_MAX_RETAIN = AUTH_OBSERVABILITY_RUNTIME_CONFIG.buffer_max_retain
_FLUSH_TRIGGER_SIZE = AUTH_OBSERVABILITY_RUNTIME_CONFIG.flush_trigger_size
_FLUSH_BATCH_SIZE = AUTH_OBSERVABILITY_RUNTIME_CONFIG.flush_batch_size
_FLUSH_INTERVAL_SECONDS = AUTH_OBSERVABILITY_RUNTIME_CONFIG.flush_interval_seconds
_DROP_LOG_INTERVAL_SECONDS = AUTH_OBSERVABILITY_RUNTIME_CONFIG.drop_log_interval_seconds
_ALLOW_SAMPLE_RATE = AUTH_OBSERVABILITY_RUNTIME_CONFIG.allow_sample_rate
_OVERLOADED_ALLOW_SAMPLE_RATE = (
    AUTH_OBSERVABILITY_RUNTIME_CONFIG.overloaded_allow_sample_rate
)
_NON_ALLOW_SAMPLE_RATE = AUTH_OBSERVABILITY_RUNTIME_CONFIG.non_allow_sample_rate
_BACKPRESSURE_SAMPLE_RATE = AUTH_OBSERVABILITY_RUNTIME_CONFIG.backpressure_sample_rate
_BACKPRESSURE_SEVERE_ACTIVE_THRESHOLD = (
    AUTH_OBSERVABILITY_RUNTIME_CONFIG.backpressure_severe_active_threshold
)


@dataclass(slots=True)
class AuthDecisionLogRecord:
    bot_id: str | None
    platform: str | None
    group_id: str | None
    user_id: str | None
    module: str | None
    effect: str
    reason: str | None = None
    latency_ms: float = 0.0
    overloaded: bool = False

    def to_model(self) -> AuthDecisionLog:
        return AuthDecisionLog(
            bot_id=self.bot_id,
            platform=self.platform,
            group_id=self.group_id,
            user_id=self.user_id,
            module=self.module,
            effect=self.effect,
            reason=self.reason,
            latency_ms=self.latency_ms,
            overloaded=self.overloaded,
        )


@dataclass(slots=True)
class RuntimeBackpressureLogRecord:
    scope_key: str | None
    reason: str | None
    lane: str | None
    action: str
    queue_size: int = 0
    active_count: int = 0
    duration_ms: float = 0.0

    def to_model(self) -> RuntimeBackpressureLog:
        return RuntimeBackpressureLog(
            scope_key=self.scope_key,
            reason=self.reason,
            lane=self.lane,
            action=self.action,
            queue_size=self.queue_size,
            active_count=self.active_count,
            duration_ms=self.duration_ms,
        )


_auth_decision_buffer: deque[AuthDecisionLogRecord] = deque()
_backpressure_buffer: deque[RuntimeBackpressureLogRecord] = deque()
_buffer_lock = asyncio.Lock()
_flush_lock = asyncio.Lock()
_flush_task: asyncio.Task[None] | None = None
_dropped = 0
_last_drop_log_at = 0.0

T = TypeVar("T")


def _ensure_flush_task() -> None:
    global _flush_task
    if _flush_task is not None and not _flush_task.done():
        return
    _flush_task = asyncio.create_task(_flush_loop())


def _record_drop() -> None:
    global _dropped, _last_drop_log_at
    _dropped += 1
    now = time.monotonic()
    if now - _last_drop_log_at < _DROP_LOG_INTERVAL_SECONDS:
        return
    _last_drop_log_at = now
    logger.warning(
        "auth observability buffer full, dropped "
        f"{_dropped} records, auth_backlog={len(_auth_decision_buffer)}, "
        f"backpressure_backlog={len(_backpressure_buffer)}",
        LOG_COMMAND,
    )


def _sample(rate: float) -> bool:
    if rate >= 1:
        return True
    if rate <= 0:
        return False
    return random.random() < rate


def _auth_decision_sample_rate(effect: str, overloaded: bool) -> float:
    if effect != "allow":
        return _NON_ALLOW_SAMPLE_RATE
    if overloaded:
        return _OVERLOADED_ALLOW_SAMPLE_RATE
    return _ALLOW_SAMPLE_RATE


def _backpressure_sample_rate(record: RuntimeBackpressureLogRecord) -> float:
    if record.reason and record.reason.startswith("hooks_"):
        return 1.0
    if record.active_count >= _BACKPRESSURE_SEVERE_ACTIVE_THRESHOLD:
        return 1.0
    if record.action in {"skip", "defer"}:
        return _BACKPRESSURE_SAMPLE_RATE
    return min(_BACKPRESSURE_SAMPLE_RATE, 0.02)


async def _append_auth_decision_record(record: AuthDecisionLogRecord) -> None:
    _ensure_flush_task()
    async with _buffer_lock:
        total = len(_auth_decision_buffer) + len(_backpressure_buffer)
        if total >= _BUFFER_MAX_RETAIN:
            if len(_auth_decision_buffer) >= len(_backpressure_buffer):
                with contextlib.suppress(IndexError):
                    _auth_decision_buffer.popleft()
            else:
                with contextlib.suppress(IndexError):
                    _backpressure_buffer.popleft()
            _record_drop()
        _auth_decision_buffer.append(record)
        should_flush = (
            len(_auth_decision_buffer) + len(_backpressure_buffer)
            >= _FLUSH_TRIGGER_SIZE
            and not _flush_lock.locked()
        )
    if should_flush:
        # Fire-and-forget keeps auth hot path independent of database stalls.
        asyncio.create_task(flush_auth_observability_buffer("缓冲区触发"))  # noqa: RUF006


async def _append_backpressure_record(record: RuntimeBackpressureLogRecord) -> None:
    _ensure_flush_task()
    async with _buffer_lock:
        total = len(_auth_decision_buffer) + len(_backpressure_buffer)
        if total >= _BUFFER_MAX_RETAIN:
            if len(_auth_decision_buffer) >= len(_backpressure_buffer):
                with contextlib.suppress(IndexError):
                    _auth_decision_buffer.popleft()
            else:
                with contextlib.suppress(IndexError):
                    _backpressure_buffer.popleft()
            _record_drop()
        _backpressure_buffer.append(record)
        should_flush = (
            len(_auth_decision_buffer) + len(_backpressure_buffer)
            >= _FLUSH_TRIGGER_SIZE
            and not _flush_lock.locked()
        )
    if should_flush:
        # Fire-and-forget keeps auth hot path independent of database stalls.
        asyncio.create_task(flush_auth_observability_buffer("缓冲区触发"))  # noqa: RUF006


async def append_auth_decision_log(
    *,
    bot_id: str | None,
    platform: str | None,
    group_id: str | None,
    user_id: str | None,
    module: str | None,
    effect: str,
    reason: str | None = None,
    latency_ms: float = 0.0,
    overloaded: bool = False,
) -> None:
    if not _sample(_auth_decision_sample_rate(effect, overloaded)):
        return
    record = AuthDecisionLogRecord(
        bot_id=bot_id,
        platform=platform,
        group_id=group_id,
        user_id=user_id,
        module=module,
        effect=effect,
        reason=(reason or "")[:255] or None,
        latency_ms=latency_ms,
        overloaded=overloaded,
    )
    await _append_auth_decision_record(record)


async def append_runtime_backpressure_log(
    *,
    scope_key: str | None,
    reason: str | None,
    lane: str | None,
    action: str,
    queue_size: int = 0,
    active_count: int = 0,
    duration_ms: float = 0.0,
) -> None:
    record = RuntimeBackpressureLogRecord(
        scope_key=(scope_key or "")[:255] or None,
        reason=(reason or "")[:255] or None,
        lane=(lane or "")[:64] or None,
        action=action,
        queue_size=queue_size,
        active_count=active_count,
        duration_ms=duration_ms,
    )
    if not _sample(_backpressure_sample_rate(record)):
        return
    await _append_backpressure_record(record)


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(_FLUSH_INTERVAL_SECONDS)
        try:
            await flush_auth_observability_buffer("定时")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("定时批量写入权限观测日志失败", LOG_COMMAND, e=exc)


async def _drain_batch(buffer: deque[T]) -> list[T]:
    batch: list[T] = []
    async with _buffer_lock:
        while buffer and len(batch) < _FLUSH_BATCH_SIZE:
            batch.append(buffer.popleft())
    return batch


async def _restore_batch(buffer: deque[T], batch: list[T]) -> None:
    async with _buffer_lock:
        retain_count = max(_BUFFER_MAX_RETAIN - len(buffer), 0)
        for record in reversed(batch[-retain_count:]):
            buffer.appendleft(record)


async def flush_auth_observability_buffer(reason: str) -> int:
    async with _flush_lock:
        written = 0
        while True:
            auth_batch = await _drain_batch(_auth_decision_buffer)
            backpressure_batch = await _drain_batch(_backpressure_buffer)
            if not auth_batch and not backpressure_batch:
                break
            try:
                if auth_batch:
                    await AuthDecisionLog.bulk_create(
                        [record.to_model() for record in auth_batch],
                        _FLUSH_BATCH_SIZE,
                    )
                    written += len(auth_batch)
                if backpressure_batch:
                    await RuntimeBackpressureLog.bulk_create(
                        [record.to_model() for record in backpressure_batch],
                        _FLUSH_BATCH_SIZE,
                    )
                    written += len(backpressure_batch)
            except Exception as exc:
                await _restore_batch(_auth_decision_buffer, auth_batch)
                await _restore_batch(_backpressure_buffer, backpressure_batch)
                logger.error(f"{reason}批量写入权限观测日志失败", LOG_COMMAND, e=exc)
                return written
        if written:
            logger.debug(f"{reason}批量写入权限观测日志 {written} 条", LOG_COMMAND)
        return written


async def stop_auth_observability_buffer() -> int:
    global _flush_task
    task = _flush_task
    _flush_task = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
    return await flush_auth_observability_buffer("关闭")


@PriorityLifecycle.on_shutdown(priority=90)
async def _flush_auth_observability_buffer_on_shutdown() -> None:
    await stop_auth_observability_buffer()

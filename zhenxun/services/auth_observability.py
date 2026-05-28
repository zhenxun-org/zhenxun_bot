from __future__ import annotations

import asyncio
from collections import deque
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import random
import time
from typing import Any, TypeVar

from tortoise import Tortoise

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
    shadow_effect: str | None = None
    shadow_reason: str | None = None
    side_effect_state: dict[str, Any] | None = None
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
            shadow_effect=self.shadow_effect,
            shadow_reason=self.shadow_reason,
            side_effect_state=json.dumps(
                self.side_effect_state,
                ensure_ascii=False,
                separators=(",", ":"),
            )[:4000]
            if self.side_effect_state
            else None,
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
_last_schema_repair_at = 0.0
_SCHEMA_REPAIR_INTERVAL_SECONDS = 300.0

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
    shadow_effect: str | None = None,
    shadow_reason: str | None = None,
    side_effect_state: dict[str, Any] | None = None,
    latency_ms: float = 0.0,
    overloaded: bool = False,
) -> None:
    if shadow_effect is None and not _sample(
        _auth_decision_sample_rate(effect, overloaded)
    ):
        return
    record = AuthDecisionLogRecord(
        bot_id=bot_id,
        platform=platform,
        group_id=group_id,
        user_id=user_id,
        module=module,
        effect=effect,
        reason=(reason or "")[:255] or None,
        shadow_effect=(shadow_effect or "")[:32] or None,
        shadow_reason=(shadow_reason or "")[:255] or None,
        side_effect_state=side_effect_state,
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


def _is_schema_mismatch_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "no column named",
            "unknown column",
            "column does not exist",
            "no such column",
        )
    )


async def _try_repair_auth_schema_once() -> bool:
    global _last_schema_repair_at
    now = time.monotonic()
    if now - _last_schema_repair_at < _SCHEMA_REPAIR_INTERVAL_SECONDS:
        return False
    _last_schema_repair_at = now
    try:
        from zhenxun.services.db_context.schema_guard import repair_table_schema

        await repair_table_schema("auth_decision_log")
        await repair_table_schema("runtime_backpressure_log")
        return True
    except Exception as exc:
        logger.warning("权限观测日志表结构自修复失败", LOG_COMMAND, e=exc)
        return False


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
                if _is_schema_mismatch_error(exc):
                    if await _try_repair_auth_schema_once():
                        try:
                            if auth_batch:
                                await AuthDecisionLog.bulk_create(
                                    [record.to_model() for record in auth_batch],
                                    _FLUSH_BATCH_SIZE,
                                )
                                written += len(auth_batch)
                            if backpressure_batch:
                                await RuntimeBackpressureLog.bulk_create(
                                    [
                                        record.to_model()
                                        for record in backpressure_batch
                                    ],
                                    _FLUSH_BATCH_SIZE,
                                )
                                written += len(backpressure_batch)
                            continue
                        except Exception as retry_exc:
                            exc = retry_exc
                    dropped = len(auth_batch) + len(backpressure_batch)
                    logger.warning(
                        f"{reason}批量写入权限观测日志遇到表结构不匹配，"
                        f"已丢弃低优先级观测日志 {dropped} 条，等待下次启动修复",
                        LOG_COMMAND,
                        e=exc,
                    )
                    return written
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


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(max(round((len(ordered) - 1) * ratio), 0), len(ordered) - 1)
    return round(ordered[index], 3)


def _bucket_counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or "<none>")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _lane_budget_advice(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        lane = str(row.get("lane") or "<unknown>")
        buckets.setdefault(lane, []).append(row)
    advice: dict[str, dict[str, Any]] = {}
    for lane, items in buckets.items():
        if lane == "<unknown>":
            continue
        durations = [float(item.get("duration_ms") or 0.0) for item in items]
        slow_waits = sum(1 for value in durations if value >= 200.0)
        active_max = max(
            (int(item.get("active_count") or 0) for item in items), default=0
        )
        total = len(items)
        if not total:
            continue
        pressure_ratio = slow_waits / total
        if pressure_ratio >= 0.2 or active_max >= 5:
            action = "increase_or_split"
        elif pressure_ratio == 0 and active_max <= 1 and total >= 20:
            action = "can_reduce"
        else:
            action = "keep"
        advice[lane] = {
            "samples": total,
            "slow_waits": slow_waits,
            "pressure_ratio": round(pressure_ratio, 3),
            "active_max": active_max,
            "p95_duration_ms": _percentile(durations, 0.95),
            "action": action,
        }
    return advice


def _query_placeholder() -> str:
    try:
        connection = Tortoise.get_connection("default")
        if (
            getattr(connection, "capabilities", None)
            and getattr(
                connection.capabilities,
                "dialect",
                "",
            )
            == "postgres"
        ):
            return "$1"
    except Exception:
        return "?"
    return "?"


async def build_auth_observability_report(*, hours: float = 24.0) -> dict[str, Any]:
    since = datetime.now() - timedelta(hours=hours)
    db = Tortoise.get_connection("default")
    placeholder = _query_placeholder()
    auth_rows = await db.execute_query_dict(
        "SELECT module, effect, reason, shadow_effect, shadow_reason, latency_ms, "
        f"overloaded FROM auth_decision_log WHERE create_time >= {placeholder} "
        "ORDER BY create_time DESC LIMIT 100000",
        [since],
    )
    backpressure_rows = await db.execute_query_dict(
        "SELECT scope_key, lane, reason, action, queue_size, active_count, duration_ms "
        f"FROM runtime_backpressure_log WHERE create_time >= {placeholder} "
        "ORDER BY create_time DESC LIMIT 100000",
        [since],
    )

    module_buckets: dict[str, list[dict[str, Any]]] = {}
    for row in auth_rows:
        module_buckets.setdefault(str(row.get("module") or "<unknown>"), []).append(row)
    module_stats: list[dict[str, Any]] = []
    for module, items in module_buckets.items():
        latencies = [float(item.get("latency_ms") or 0.0) for item in items]
        module_stats.append(
            {
                "module": module,
                "total": len(items),
                "effects": _bucket_counts(items, "effect"),
                "shadow_effects": _bucket_counts(items, "shadow_effect"),
                "avg_latency_ms": round(sum(latencies) / len(latencies), 3)
                if latencies
                else 0.0,
                "p95_latency_ms": _percentile(latencies, 0.95),
                "overloaded": sum(1 for item in items if bool(item.get("overloaded"))),
            }
        )

    backpressure_buckets: dict[str, list[dict[str, Any]]] = {}
    for row in backpressure_rows:
        key = f"{row.get('lane') or '<unknown>'}:{row.get('reason') or '<none>'}"
        backpressure_buckets.setdefault(key, []).append(row)
    backpressure_stats: list[dict[str, Any]] = []
    for key, items in backpressure_buckets.items():
        durations = [float(item.get("duration_ms") or 0.0) for item in items]
        backpressure_stats.append(
            {
                "key": key,
                "total": len(items),
                "actions": _bucket_counts(items, "action"),
                "avg_duration_ms": round(sum(durations) / len(durations), 3)
                if durations
                else 0.0,
                "p95_duration_ms": _percentile(durations, 0.95),
            }
        )

    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "window_hours": hours,
        "auth_decisions": {
            "total": len(auth_rows),
            "effects": _bucket_counts(auth_rows, "effect"),
            "shadow_effects": _bucket_counts(auth_rows, "shadow_effect"),
            "top_modules_by_p95": sorted(
                module_stats,
                key=lambda item: (item["p95_latency_ms"], item["total"]),
                reverse=True,
            )[:30],
        },
        "backpressure": {
            "total": len(backpressure_rows),
            "lane_budget_advice": _lane_budget_advice(backpressure_rows),
            "top_reasons": sorted(
                backpressure_stats,
                key=lambda item: (item["total"], item["p95_duration_ms"]),
                reverse=True,
            )[:30],
        },
    }


@PriorityLifecycle.on_shutdown(priority=90)
async def _flush_auth_observability_buffer_on_shutdown() -> None:
    await stop_auth_observability_buffer()

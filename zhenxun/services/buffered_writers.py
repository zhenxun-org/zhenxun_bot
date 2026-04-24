from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import time

from zhenxun.models.user_gold_log import UserGoldLog
from zhenxun.services.log import logger
from zhenxun.utils.enum import GoldHandle
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

LOG_COMMAND = "BufferedWriters"

_USER_GOLD_LOG_BUFFER_MAX_RETAIN = 10_000
_USER_GOLD_LOG_FLUSH_TRIGGER_SIZE = 128
_USER_GOLD_LOG_FLUSH_BATCH_SIZE = 500
_USER_GOLD_LOG_FLUSH_INTERVAL_SECONDS = 60.0
_USER_GOLD_LOG_DROP_LOG_INTERVAL_SECONDS = 10.0

_user_gold_log_buffer: deque[UserGoldLog] = deque()
_user_gold_log_buffer_lock = asyncio.Lock()
_user_gold_log_flush_lock = asyncio.Lock()
_user_gold_log_flush_task: asyncio.Task[None] | None = None
_user_gold_log_dropped = 0
_user_gold_log_last_drop_log_at = 0.0


def _ensure_user_gold_log_flush_task() -> None:
    global _user_gold_log_flush_task
    if _user_gold_log_flush_task is not None and not _user_gold_log_flush_task.done():
        return
    _user_gold_log_flush_task = asyncio.create_task(_user_gold_log_flush_loop())


def _record_user_gold_log_drop() -> None:
    global _user_gold_log_dropped, _user_gold_log_last_drop_log_at
    _user_gold_log_dropped += 1
    now = time.monotonic()
    if now - _user_gold_log_last_drop_log_at < _USER_GOLD_LOG_DROP_LOG_INTERVAL_SECONDS:
        return
    _user_gold_log_last_drop_log_at = now
    logger.warning(
        "user_gold_log buffer full, dropped "
        f"{_user_gold_log_dropped} records, backlog={len(_user_gold_log_buffer)}",
        LOG_COMMAND,
    )


async def _user_gold_log_flush_loop() -> None:
    while True:
        await asyncio.sleep(_USER_GOLD_LOG_FLUSH_INTERVAL_SECONDS)
        try:
            await flush_user_gold_log_buffer("定时")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("定时批量写入金币流水失败", LOG_COMMAND, e=exc)


async def append_user_gold_log(
    user_id: str,
    gold: int,
    handle: GoldHandle,
    source: str | None = None,
) -> None:
    _ensure_user_gold_log_flush_task()
    record = UserGoldLog(user_id=user_id, gold=gold, handle=handle, source=source)
    async with _user_gold_log_buffer_lock:
        if len(_user_gold_log_buffer) >= _USER_GOLD_LOG_BUFFER_MAX_RETAIN:
            _user_gold_log_buffer.popleft()
            _record_user_gold_log_drop()
        _user_gold_log_buffer.append(record)
        should_flush = (
            len(_user_gold_log_buffer) >= _USER_GOLD_LOG_FLUSH_TRIGGER_SIZE
            and not _user_gold_log_flush_lock.locked()
        )
    if should_flush:
        await flush_user_gold_log_buffer("缓冲区触发")


async def flush_user_gold_log_buffer(reason: str) -> int:
    async with _user_gold_log_flush_lock:
        written = 0
        while True:
            batch: list[UserGoldLog] = []
            async with _user_gold_log_buffer_lock:
                if not _user_gold_log_buffer:
                    break
                while (
                    _user_gold_log_buffer
                    and len(batch) < _USER_GOLD_LOG_FLUSH_BATCH_SIZE
                ):
                    batch.append(_user_gold_log_buffer.popleft())
            if not batch:
                break
            try:
                await UserGoldLog.bulk_create(batch, _USER_GOLD_LOG_FLUSH_BATCH_SIZE)
            except Exception as exc:
                async with _user_gold_log_buffer_lock:
                    retain_count = max(
                        _USER_GOLD_LOG_BUFFER_MAX_RETAIN - len(_user_gold_log_buffer),
                        0,
                    )
                    for record in reversed(batch[-retain_count:]):
                        _user_gold_log_buffer.appendleft(record)
                logger.error(f"{reason}批量写入金币流水失败", LOG_COMMAND, e=exc)
                return written
            written += len(batch)
        if written:
            logger.debug(f"{reason}批量写入金币流水 {written} 条", LOG_COMMAND)
        return written


async def stop_user_gold_log_buffer() -> int:
    global _user_gold_log_flush_task
    task = _user_gold_log_flush_task
    _user_gold_log_flush_task = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
    return await flush_user_gold_log_buffer("关闭")


@PriorityLifecycle.on_shutdown(priority=90)
async def _flush_user_gold_log_buffer_on_shutdown() -> None:
    await stop_user_gold_log_buffer()

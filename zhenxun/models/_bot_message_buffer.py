from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import time
from typing import TYPE_CHECKING

from zhenxun.services.log import logger

if TYPE_CHECKING:
    from .bot_message_store import BotMessageStore

LOG_COMMAND = "BotMessageStore"

_BUFFER_MAX_RETAIN = 20_000
_FLUSH_TRIGGER_SIZE = 64
_FLUSH_BATCH_SIZE = 500
_FLUSH_INTERVAL_SECONDS = 5.0
_DROP_LOG_INTERVAL_SECONDS = 10.0

_buffer: deque[BotMessageStore] = deque()
_buffer_lock = asyncio.Lock()
_flush_lock = asyncio.Lock()
_flush_task: asyncio.Task[None] | None = None
_dropped = 0
_last_drop_log_at = 0.0


def _ensure_flush_task() -> None:
    global _flush_task
    if _flush_task is not None and not _flush_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _flush_task = loop.create_task(_flush_loop())


def _record_drop() -> None:
    global _dropped, _last_drop_log_at
    _dropped += 1
    now = time.monotonic()
    if now - _last_drop_log_at < _DROP_LOG_INTERVAL_SECONDS:
        return
    _last_drop_log_at = now
    logger.warning(
        f"bot_message_store buffer full, dropped {_dropped} records, "
        f"backlog={len(_buffer)}",
        LOG_COMMAND,
    )


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(_FLUSH_INTERVAL_SECONDS)
        try:
            await flush_bot_message_store_buffer("定时")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("定时批量写入 Bot 发送记录失败", LOG_COMMAND, e=exc)


async def append_bot_message_store_record(record: BotMessageStore) -> None:
    _ensure_flush_task()
    async with _buffer_lock:
        if len(_buffer) >= _BUFFER_MAX_RETAIN:
            _buffer.popleft()
            _record_drop()
        _buffer.append(record)
        should_flush = len(_buffer) >= _FLUSH_TRIGGER_SIZE and not _flush_lock.locked()
    if should_flush:
        await flush_bot_message_store_buffer("缓冲区触发")


async def flush_bot_message_store_buffer(reason: str) -> int:
    from .bot_message_store import BotMessageStore

    async with _flush_lock:
        written = 0
        while True:
            batch: list[BotMessageStore] = []
            async with _buffer_lock:
                while _buffer and len(batch) < _FLUSH_BATCH_SIZE:
                    batch.append(_buffer.popleft())
            if not batch:
                break
            try:
                await BotMessageStore.bulk_create(batch, batch_size=_FLUSH_BATCH_SIZE)
            except Exception as exc:
                async with _buffer_lock:
                    retain_count = max(_BUFFER_MAX_RETAIN - len(_buffer), 0)
                    for record in reversed(batch[-retain_count:]):
                        _buffer.appendleft(record)
                logger.error(f"{reason}批量写入 Bot 发送记录失败", LOG_COMMAND, e=exc)
                return written
            written += len(batch)
        if written:
            logger.debug(f"{reason}批量写入 Bot 发送记录 {written} 条", LOG_COMMAND)
        return written


async def stop_bot_message_store_buffer() -> int:
    global _flush_task
    task = _flush_task
    _flush_task = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
    return await flush_bot_message_store_buffer("关闭")

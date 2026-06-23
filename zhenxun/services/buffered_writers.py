from __future__ import annotations

from zhenxun.models.user_gold_log import UserGoldLog
from zhenxun.services.low_priority_writer import (
    LowPriorityWriterConfig,
    append_low_priority_record,
    flush_low_priority_writer,
    register_low_priority_writer,
)
from zhenxun.utils.enum import GoldHandle

LOG_COMMAND = "BufferedWriters"

_WRITER_NAME = "user_gold_log"
_USER_GOLD_LOG_BUFFER_MAX_RETAIN = 10_000
_USER_GOLD_LOG_FLUSH_TRIGGER_SIZE = 128
_USER_GOLD_LOG_FLUSH_BATCH_SIZE = 500
_USER_GOLD_LOG_FLUSH_INTERVAL_SECONDS = 60.0


async def _write_user_gold_log_batch(
    batch: list[UserGoldLog],
    reason: str,
) -> None:
    from zhenxun.services.db_context import with_db_timeout

    await with_db_timeout(
        UserGoldLog.bulk_create(batch, _USER_GOLD_LOG_FLUSH_BATCH_SIZE),
        timeout=5.0,
        operation=f"UserGoldLog.bulk_create[{len(batch)}]",
        source=f"user_gold_log:{reason}",
    )


register_config = LowPriorityWriterConfig(
    name=_WRITER_NAME,
    write_batch=_write_user_gold_log_batch,
    batch_size=_USER_GOLD_LOG_FLUSH_BATCH_SIZE,
    trigger_size=_USER_GOLD_LOG_FLUSH_TRIGGER_SIZE,
    max_retain=_USER_GOLD_LOG_BUFFER_MAX_RETAIN,
    flush_interval_seconds=_USER_GOLD_LOG_FLUSH_INTERVAL_SECONDS,
    max_items_per_cycle=_USER_GOLD_LOG_FLUSH_BATCH_SIZE,
    backoff_base_seconds=30.0,
    backoff_max_seconds=600.0,
    log_command=LOG_COMMAND,
)


async def append_user_gold_log(
    user_id: str,
    gold: int,
    handle: GoldHandle,
    source: str | None = None,
) -> None:
    record = UserGoldLog(user_id=user_id, gold=gold, handle=handle, source=source)
    await append_low_priority_record(_WRITER_NAME, record)


async def flush_user_gold_log_buffer(reason: str) -> int:
    return await flush_low_priority_writer(_WRITER_NAME, reason, force=True)


async def stop_user_gold_log_buffer() -> int:
    return await flush_user_gold_log_buffer("关闭")


register_low_priority_writer(register_config)

import asyncio
import contextlib
import time

from zhenxun.services.log import logger
from zhenxun.services.message_load import signal_db_unhealthy

from .config import (
    DB_TIMEOUT_SECONDS,
    LOG_COMMAND,
    SLOW_QUERY_THRESHOLD,
)

_SQLITE_STALL_UNTIL = 0.0
_SQLITE_STALL_REASON = ""
_DB_UNHEALTHY_TIMEOUT_SECONDS = 30.0
_SQLITE_STALL_TIMEOUT_SECONDS = 60.0


def _is_sqlite_connection() -> bool:
    with contextlib.suppress(Exception):
        from tortoise import Tortoise

        connection = Tortoise.get_connection("default")
        capabilities = getattr(connection, "capabilities", None)
        dialect = str(getattr(capabilities, "dialect", "") or "").lower()
        return dialect.startswith("sqlite")
    return False


def _mark_sqlite_stall(reason: str, duration: float) -> None:
    global _SQLITE_STALL_REASON, _SQLITE_STALL_UNTIL
    until = time.monotonic() + max(duration, 0.0)
    if until > _SQLITE_STALL_UNTIL:
        _SQLITE_STALL_UNTIL = until
        _SQLITE_STALL_REASON = str(reason or "")[:200]


def is_sqlite_stall_suspected() -> bool:
    return time.monotonic() < _SQLITE_STALL_UNTIL


def sqlite_stall_reason() -> str:
    if not is_sqlite_stall_suspected():
        return ""
    return _SQLITE_STALL_REASON


async def with_db_timeout(
    coro,
    timeout: float = DB_TIMEOUT_SECONDS,
    operation: str | None = None,
    source: str | None = None,
):
    """带超时控制的数据库操作"""
    start_time = time.time()
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        elapsed = time.time() - start_time
        if elapsed > SLOW_QUERY_THRESHOLD and operation:
            logger.warning(f"慢查询: {operation} 耗时 {elapsed:.3f}s", LOG_COMMAND)
        return result
    except asyncio.TimeoutError:
        timeout_reason = f"{operation or 'database_operation'} from {source or '-'}"
        unhealthy_duration = _DB_UNHEALTHY_TIMEOUT_SECONDS
        if _is_sqlite_connection():
            unhealthy_duration = _SQLITE_STALL_TIMEOUT_SECONDS
            _mark_sqlite_stall(timeout_reason, unhealthy_duration)
            logger.warning(
                "SQLite 数据库操作超时，疑似 aiosqlite worker/连接被锁等待卡住；"
                "已暂停低优先级数据库任务",
                LOG_COMMAND,
            )
        signal_db_unhealthy(unhealthy_duration, reason=timeout_reason)
        if operation:
            logger.error(
                f"数据库操作超时: {operation} (>{timeout}s) 来源: {source}",
                LOG_COMMAND,
            )
        raise

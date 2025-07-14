import asyncio
import time

from zhenxun.services.log import logger

from .config import (
    DB_TIMEOUT_SECONDS,
    LOG_COMMAND,
    SLOW_QUERY_THRESHOLD,
)


async def with_db_timeout(
    coro, timeout: float = DB_TIMEOUT_SECONDS, operation: str | None = None
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
        if operation:
            logger.error(f"数据库操作超时: {operation} (>{timeout}s)", LOG_COMMAND)
        raise

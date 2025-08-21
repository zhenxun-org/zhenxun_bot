import asyncio
import time

from zhenxun.services.cache import CacheRoot
from zhenxun.services.log import logger

from .config import (
    DB_TIMEOUT_SECONDS,
    LOG_COMMAND,
    SLOW_QUERY_THRESHOLD,
)

cache = CacheRoot.cache_dict("DB_TEST", 10)

index = 0


total = {}


async def with_db_timeout(
    coro,
    timeout: float = DB_TIMEOUT_SECONDS,
    operation: str | None = None,
    source: str | None = None,
):
    """带超时控制的数据库操作"""
    global index, total, index2
    start_time = time.time()
    try:
        if operation not in total:
            total[operation] = CacheRoot.cache_dict(f"DB_TEST_{operation}", 10)
        total[operation][str(index)] = "1"
        index += 1
        logger.info(f"当前数据库查询来源: {source}")
        logger.info(
            f"10秒内数据库查询次数 [{operation}]: {len(total[operation])}", "DB_TEST"
        )
        result = await asyncio.wait_for(coro, timeout=timeout)
        cache[str(index)] = "1"
        index += 1
        elapsed = time.time() - start_time
        if elapsed > SLOW_QUERY_THRESHOLD and operation:
            logger.warning(f"慢查询: {operation} 耗时 {elapsed:.3f}s", LOG_COMMAND)
        return result
    except asyncio.TimeoutError:
        if operation:
            logger.error(f"数据库操作超时: {operation} (>{timeout}s)", LOG_COMMAND)
        raise

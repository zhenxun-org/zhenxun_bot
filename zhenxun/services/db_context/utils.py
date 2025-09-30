import asyncio
import random
import time

from zhenxun.services.log import logger

from ..cache import CacheRoot
from .config import (
    DB_TIMEOUT_SECONDS,
    LOG_COMMAND,
    SLOW_QUERY_THRESHOLD,
)

aaa = CacheRoot.cache_dict("AAA", 10, int)


async def with_db_timeout(
    coro,
    timeout: float = DB_TIMEOUT_SECONDS,
    operation: str | None = None,
    source: str | None = None,
):
    """带超时控制的数据库操作"""
    start_time = time.time()
    try:
        logger.debug(f"开始执行数据库操作: {operation} 来源: {source}")
        result = await asyncio.wait_for(coro, timeout=timeout)
        aaa[str(random.randint(0, 100000))] = 1
        elapsed = time.time() - start_time
        if elapsed > SLOW_QUERY_THRESHOLD and operation:
            logger.warning(f"慢查询: {operation} 耗时 {elapsed:.3f}s", LOG_COMMAND)
        logger.debug(
            f"10s内数据库查询次数: {len(aaa)}， 开始执行数据库操作: {operation} "
            f"来源: {source}, 当次返回数据ID: {getattr(result, 'id', None)}",
            LOG_COMMAND,
        )
        return result
    except asyncio.TimeoutError:
        if operation:
            logger.error(
                f"数据库操作超时: {operation} (>{timeout}s) 来源: {source}",
                LOG_COMMAND,
            )
        raise

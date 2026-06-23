import asyncio
from datetime import datetime

from nonebot import get_driver
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import PokeNotifyEvent
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor
from nonebot.plugin import PluginMetadata
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.statistics import Statistics
from zhenxun.services.cache.runtime_cache import PluginInfoMemoryCache
from zhenxun.services.db_context import with_db_timeout
from zhenxun.services.log import logger
from zhenxun.services.low_priority_writer import (
    LowPriorityWriterConfig,
    append_low_priority_record,
    flush_low_priority_writer,
    register_low_priority_writer,
)
from zhenxun.utils.enum import PluginType
from zhenxun.utils.utils import get_entity_ids

__plugin_meta__ = PluginMetadata(
    name="功能调用统计",
    description="功能调用统计",
    usage="""""".strip(),
    extra=PluginExtraData(
        author="HibiKier", version="0.1", plugin_type=PluginType.HIDDEN
    ).to_dict(),
)

STATS_BUFFER_FLUSH_SIZE = 5000
STATS_BUFFER_MAX_RETAIN = 10000
_STATS_FLUSH_LOCK = asyncio.Lock()
_WRITER_NAME = "statistics"
driver = get_driver()


async def _write_statistics_batch(batch: list[Statistics], reason: str) -> None:
    await with_db_timeout(
        Statistics.bulk_create(batch),
        timeout=5.0,
        operation=f"Statistics.bulk_create[{len(batch)}]",
        source=f"statistics:{reason}",
    )


register_low_priority_writer(
    LowPriorityWriterConfig(
        name=_WRITER_NAME,
        write_batch=_write_statistics_batch,
        batch_size=STATS_BUFFER_FLUSH_SIZE,
        trigger_size=STATS_BUFFER_FLUSH_SIZE,
        max_retain=STATS_BUFFER_MAX_RETAIN,
        flush_interval_seconds=30 * 60,
        max_items_per_cycle=STATS_BUFFER_FLUSH_SIZE,
        backoff_base_seconds=30.0,
        backoff_max_seconds=600.0,
        log_command="定时任务",
    )
)


async def _flush_statistics_buffer(reason: str) -> int:
    """Compatibility entry used by memory governor and shutdown hooks."""
    async with _STATS_FLUSH_LOCK:
        return await flush_low_priority_writer(_WRITER_NAME, reason, force=True)


async def _append_statistics(record: Statistics) -> None:
    await append_low_priority_record(_WRITER_NAME, record)


@run_postprocessor
async def _(
    matcher: Matcher,
    exception: Exception | None,
    bot: Bot,
    session: Uninfo,
    event: Event,
):
    if matcher.type == "notice" and not isinstance(event, PokeNotifyEvent):
        """过滤除poke外的notice"""
        return
    if matcher.plugin:
        plugin = PluginInfoMemoryCache.get_by_module_path(matcher.plugin.module_name)
        if not plugin:
            # cache miss 时不查数据库，直接跳过统计，避免阻塞
            return
        if plugin.ignore_statistics:
            return
        plugin_type = plugin.plugin_type
        if plugin_type == PluginType.NORMAL:
            entity = get_entity_ids(session)
            logger.debug(f"提交调用记录: {matcher.plugin_name}...", session=session)
            await _append_statistics(
                Statistics(
                    user_id=entity.user_id,
                    group_id=entity.group_id,
                    plugin_name=matcher.plugin_name,
                    create_time=datetime.now(),
                    bot_id=bot.self_id,
                )
            )


@driver.on_shutdown
async def _flush_statistics_on_shutdown():
    await _flush_statistics_buffer("关闭")

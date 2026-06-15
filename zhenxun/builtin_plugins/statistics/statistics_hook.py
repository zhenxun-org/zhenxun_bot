import asyncio
from datetime import datetime

from nonebot import get_driver
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import PokeNotifyEvent
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor
from nonebot.plugin import PluginMetadata
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.statistics import Statistics
from zhenxun.services.cache.runtime_cache import PluginInfoMemoryCache
from zhenxun.services.log import logger
from zhenxun.services.message_load import should_pause_tasks
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
TEMP_LIST: list[Statistics] = []
_STATS_FLUSH_LOCK = asyncio.Lock()
driver = get_driver()


async def _flush_statistics_buffer(reason: str) -> int:
    async with _STATS_FLUSH_LOCK:
        call_list = TEMP_LIST.copy()
        TEMP_LIST.clear()
        if not call_list:
            return 0
        try:
            await Statistics.bulk_create(call_list)
        except Exception as e:
            logger.error(f"{reason}批量添加调用记录失败", "定时任务", e=e)
            retain_count = max(STATS_BUFFER_MAX_RETAIN - len(TEMP_LIST), 0)
            if retain_count:
                TEMP_LIST[:0] = call_list[-retain_count:]
            return 0
    logger.debug(f"{reason}批量添加调用记录 {len(call_list)} 条", "定时任务")
    return len(call_list)


async def _append_statistics(record: Statistics) -> None:
    # 在锁内追加(B8),与 flush 的 copy+clear 串行,消除逻辑窗口;
    # flush 自身再次获取同一把锁,故在锁外触发避免重入。
    async with _STATS_FLUSH_LOCK:
        TEMP_LIST.append(record)
        should_flush = len(TEMP_LIST) >= STATS_BUFFER_FLUSH_SIZE
    if should_flush:
        await _flush_statistics_buffer("缓冲区触发")


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


@scheduler.scheduled_job("interval", minutes=30, max_instances=1, coalesce=True)
async def _():
    try:
        if should_pause_tasks():
            return
        await _flush_statistics_buffer("定时")
    except Exception as e:
        logger.error("定时批量添加调用记录", "定时任务", e=e)


@driver.on_shutdown
async def _flush_statistics_on_shutdown():
    await _flush_statistics_buffer("关闭")

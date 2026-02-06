from datetime import datetime

from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import PokeNotifyEvent
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor
from nonebot.plugin import PluginMetadata
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.plugin_info import PluginInfo
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

TEMP_LIST = []


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
        entity = get_entity_ids(session)
        plugin = PluginInfoMemoryCache.get_by_module_path(matcher.plugin.module_name)
        if not plugin:
            plugin = await PluginInfo.get_plugin(module_path=matcher.plugin.module_name)
            if plugin:
                PluginInfoMemoryCache.set_plugin(plugin)
        if plugin and plugin.ignore_statistics:
            return
        plugin_type = plugin.plugin_type if plugin else None
        if plugin_type == PluginType.NORMAL:
            logger.debug(f"提交调用记录: {matcher.plugin_name}...", session=session)
            TEMP_LIST.append(
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
        call_list = TEMP_LIST.copy()
        TEMP_LIST.clear()
        if call_list:
            await Statistics.bulk_create(call_list)
        logger.debug(f"批量添加调用记录 {len(call_list)} 条", "定时任务")
    except Exception as e:
        logger.error("定时批量添加调用记录", "定时任务", e=e)

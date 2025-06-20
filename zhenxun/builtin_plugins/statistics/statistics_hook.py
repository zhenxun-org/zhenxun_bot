from datetime import datetime

from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import PokeNotifyEvent
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor
from nonebot.plugin import PluginMetadata
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_session import EventSession

from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.statistics import Statistics
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType

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
    session: EventSession,
    event: Event,
):
    if matcher.type == "notice" and not isinstance(event, PokeNotifyEvent):
        """过滤除poke外的notice"""
        return
    if session.id1 and matcher.plugin:
        plugin = await PluginInfo.get_plugin(module_path=matcher.plugin.module_name)
        plugin_type = plugin.plugin_type if plugin else None
        if plugin_type == PluginType.NORMAL:
            logger.debug(f"提交调用记录: {matcher.plugin_name}...", session=session)
            TEMP_LIST.append(
                Statistics(
                    user_id=session.id1,
                    group_id=session.id3 or session.id2,
                    plugin_name=matcher.plugin_name,
                    create_time=datetime.now(),
                    bot_id=bot.self_id,
                )
            )


@scheduler.scheduled_job("interval", minutes=1, max_instances=5)
async def _():
    try:
        call_list = TEMP_LIST.copy()
        TEMP_LIST.clear()
        if call_list:
            await Statistics.bulk_create(call_list)
        logger.debug(f"批量添加调用记录 {len(call_list)} 条", "定时任务")
    except Exception as e:
        logger.error("定时批量添加调用记录", "定时任务", e=e)

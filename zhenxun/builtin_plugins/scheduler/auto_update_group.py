import nonebot
from nonebot_plugin_apscheduler import scheduler

from zhenxun.services.log import logger
from zhenxun.services.message_load import should_pause_tasks
from zhenxun.services.tags import tag_manager
from zhenxun.utils.platform import PlatformUtils


# 自动更新群组信息
@scheduler.scheduled_job(
    "cron",
    hour=3,
    minute=1,
)
async def _():
    if should_pause_tasks():
        return
    bots = nonebot.get_bots()
    for bot in bots.values():
        if PlatformUtils.get_platform_scope(bot) != "qq_client":
            continue
        try:
            await PlatformUtils.update_group(bot)
        except Exception as e:
            logger.error(f"Bot: {bot.self_id} 自动更新群组信息", "自动更新群组", e=e)
    logger.info("自动更新群组成员信息成功...")


# 自动更新好友信息
@scheduler.scheduled_job(
    "cron",
    hour=3,
    minute=1,
)
async def _():
    if should_pause_tasks():
        return
    bots = nonebot.get_bots()
    for bot in bots.values():
        if PlatformUtils.get_platform_scope(bot) != "qq_client":
            continue
        try:
            await PlatformUtils.update_friend(bot)
        except Exception as e:
            logger.error(
                f"Bot: {bot.self_id} 自动更新好友信息错误", "自动更新好友", e=e
            )
    logger.info("自动更新好友信息成功...")


# 自动清理静态标签中的无效群组
@scheduler.scheduled_job(
    "cron",
    hour=4,
    minute=50,
)
async def _prune_stale_tags():
    deleted_count = await tag_manager.prune_stale_group_links()
    if deleted_count > 0:
        logger.info(
            f"定时任务：成功清理了 {deleted_count} 个无效的群组标签" f"关联。",
            "群组标签管理",
        )
    else:
        logger.debug("定时任务：未发现无效的群组标签关联。", "群组标签管理")

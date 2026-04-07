from pathlib import Path

import nonebot
from nonebot.adapters import Bot
from nonebot_plugin_apscheduler import scheduler

from zhenxun.configs.config import Config
from zhenxun.models.chat_history import ChatHistory
from zhenxun.models.group_console import GroupConsole
from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.platform import PlatformUtils

from .__init_cache import register_cache_types

nonebot.load_plugins(str(Path(__file__).parent.resolve()))


driver = nonebot.get_driver()


Config.add_plugin_config(
    "auto_clean",
    "CLEAN_CHAT_HISTORY",
    True,
    help="是否自动清理已退出群聊的聊天记录",
    default_value=True,
    type=bool,
)


@PriorityLifecycle.on_startup(priority=5)
async def _():
    register_cache_types()
    logger.info("缓存类型注册完成")


@driver.on_bot_connect
async def _(bot: Bot):
    """同步 Bot 已存在的群组到 GroupConsole，并清理已退出的群

    参数:
        bot: Bot
    """
    if PlatformUtils.get_platform(bot) != "qq":
        return

    logger.debug(f"更新Bot: {bot.self_id} 的群认证...", "群认证同步")

    # 实际在用的群列表（当前 bot 连接可见的群）
    current_group_list, _ = await PlatformUtils.get_group_list(bot)
    current_group_ids = {g.group_id for g in current_group_list}

    # 数据库中已有的群记录
    db_group_list: list[str] = await GroupConsole.all().values_list(
        "group_id", flat=True
    )  # pyright: ignore[reportAssignmentType]
    db_group_ids = set(db_group_list)

    # 需要创建的群（当前存在，但数据库中没有）
    create_list = []
    for group in current_group_list:
        if group.group_id not in db_group_ids:
            group.group_flag = 1
            create_list.append(group)

    if create_list:
        await GroupConsole.bulk_create(create_list, 10)

    if delete_ids := list(db_group_ids - current_group_ids):
        deleted_count = await GroupConsole.filter(group_id__in=delete_ids).delete()
    else:
        deleted_count = 0
    logger.info(
        f"更新Bot: {bot.self_id} 的群认证完成，共创建 {len(create_list)} 条数据，"
        f"删除 {deleted_count} 条已退出群组的数据...",
        "群认证同步",
    )

    if Config.get_config("auto_clean", "CLEAN_CHAT_HISTORY"):
        # 清理已退出群组的聊天记录
        scheduler.add_job(
            clean_chat_history,
            "cron",
            hour=1,
            minute=0,
            args=(current_group_list,),
            id="clean_chat_history",
            replace_existing=True,
        )


async def clean_chat_history(
    group_list: list[GroupConsole],
    max_delete: int = 2000,
):
    """清理已退出群组的聊天记录

    为避免一次调用删除过多数据，单次调用最多删除 max_delete 条。
    """
    # 将传入的对象统一转成 group_id 字符串列表
    group_ids: list[str] = [g.group_id for g in group_list]

    if not group_ids:
        logger.warning("传入群组列表为空，跳过清理", "定时清理群组聊天记录")
        return

    # 只取最多 max_delete 条记录的 id，然后删除这些记录，避免一次删太多
    ids = (
        await ChatHistory.filter(group_id__not_in=group_ids)
        .limit(max_delete)
        .values_list("id", flat=True)
    )
    ids = list(ids)
    if not ids:
        logger.info(
            f"群组数 {len(group_ids)}，无聊天记录可删除", "定时清理群组聊天记录"
        )
        return

    await ChatHistory.filter(id__in=ids).delete()

    logger.success(f"已清理 {len(ids)} 条已退出群组的聊天记录", "定时清理群组聊天记录")

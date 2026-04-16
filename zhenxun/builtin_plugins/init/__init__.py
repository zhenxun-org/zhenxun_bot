from datetime import datetime
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

    logger.info(
        f"更新Bot: {bot.self_id} 的群认证完成，共创建 {len(create_list)} 条数据..."
        "群认证同步",
    )

    if Config.get_config("auto_clean", "CLEAN_CHAT_HISTORY"):
        await _update_global_group_cache({g.group_id for g in current_group_list})
        # 清理已退出群组的聊天记录
        scheduler.add_job(
            clean_chat_history,
            "cron",
            hour=1,
            minute=0,
            id="clean_chat_history_cron",
            replace_existing=True,
        )

        scheduler.add_job(
            clean_chat_history,
            "date",
            run_date=datetime.now(),
            id="clean_chat_history_immediate",
            replace_existing=True,
        )


# 用于在多 Bot 场景下聚合“所有 Bot 当前仍在的群号”
_GLOBAL_ACTIVE_GROUP_IDS: set[str] = set()


async def _update_global_group_cache(current_ids: set[str]) -> None:
    """更新全局活跃群组缓存。

    参数:
        current_ids: 某个 Bot 当前仍然存在的群号集合
    """
    # 这里简单地做 union：某个群只要任一 Bot 还在，就会出现在全局集合中，
    # 清理时只会删除不在该集合中的群记录，不会误删其它 Bot 仍在的群。
    _GLOBAL_ACTIVE_GROUP_IDS.update(current_ids)


async def clean_chat_history(
    max_delete: int = 2000,
):
    """清理已退出群组的聊天记录。

    为避免一次调用删除过多数据，单次调用最多删除 max_delete 条。
    在多 Bot 场景下，会使用所有 Bot 的活跃群组 union 作为保留白名单：
    只有对所有 Bot 都已退出的群，聊天记录才会被清理。
    """
    if not _GLOBAL_ACTIVE_GROUP_IDS:
        logger.warning("全局活跃群组集合为空，跳过清理", "定时清理群组聊天记录")
        return

    group_ids = list(_GLOBAL_ACTIVE_GROUP_IDS)

    # 只取最多 max_delete 条记录的 id，然后删除这些记录，避免一次删太多
    # 优先删除最旧的记录：按 create_time 升序
    ids = (
        await ChatHistory.filter(group_id__not_in=group_ids)
        .order_by("create_time")
        .limit(max_delete)
        .values_list("id", flat=True)
    )
    ids = list(ids)
    if not ids:
        logger.info(
            f"活跃群组数 {len(group_ids)}，无聊天记录可删除", "定时清理群组聊天记录"
        )
        return

    await ChatHistory.filter(id__in=ids).delete()

    logger.success(
        f"已清理 {len(ids)} 条所有 Bot 均已退出群组的聊天记录",
        "定时清理群组聊天记录",
    )

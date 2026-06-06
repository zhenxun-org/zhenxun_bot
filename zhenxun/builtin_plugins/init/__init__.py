from pathlib import Path

import nonebot
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11.exception import NetworkError

from zhenxun.models.group_console import GroupConsole
from zhenxun.services.log import logger
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.platform import PlatformUtils

from .__init_cache import register_cache_types

nonebot.load_plugins(str(Path(__file__).parent.resolve()))


driver = nonebot.get_driver()


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
    if PlatformUtils.get_platform_scope(bot) != "qq_client":
        return

    logger.debug(f"更新Bot: {bot.self_id} 的群认证...", "群认证同步")

    try:
        current_group_list, _ = await PlatformUtils.get_group_list(bot)
    except NetworkError as e:
        logger.debug(
            f"Bot: {bot.self_id} 群认证同步被连接关闭打断，跳过本次同步: {e}",
            "群认证同步",
        )
        return

    if not current_group_list:
        logger.warning(
            f"Bot: {bot.self_id} 未获取到任何群组，"
            "本次不会创建群认证；后续群消息将尝试按事件自愈。",
            "群认证同步",
        )

    db_group_list: list[str] = await GroupConsole.all().values_list(
        "group_id", flat=True
    )  # pyright: ignore[reportAssignmentType]
    db_group_ids = set(db_group_list)

    create_list = []
    for group in current_group_list:
        if group.group_id not in db_group_ids:
            group.group_flag = 1
            create_list.append(group)

    if create_list:
        await GroupConsole.bulk_create(create_list, 10)
        task_modules = await GroupConsole._get_task_modules(default_status=False)
        plugin_modules = await GroupConsole._get_plugin_modules(default_status=False)
        new_ids = [g.group_id for g in create_list]
        fresh = await GroupConsole.filter(group_id__in=new_ids).all()
        if task_modules or plugin_modules:
            for group in fresh:
                await GroupConsole._update_modules(group, task_modules, plugin_modules)
        from zhenxun.services.cache.runtime_cache import GroupMemoryCache

        for group in fresh:
            await GroupMemoryCache.upsert_from_model(group)

    logger.info(
        f"更新Bot: {bot.self_id} 的群认证完成，共创建 {len(create_list)} 条数据，",
        "群认证同步",
    )

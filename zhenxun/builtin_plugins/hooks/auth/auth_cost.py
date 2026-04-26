import time

from nonebot_plugin_uninfo import Uninfo

from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.user_console import UserConsole
from zhenxun.services.log import logger

from .config import LOGGER_COMMAND, WARNING_THRESHOLD
from .context import PermissionContext
from .exception import SkipPluginException

DEFAULT_GOLD = 100


async def auth_cost(
    user: UserConsole | None,
    plugin: PluginInfo,
    session: Uninfo,
    *,
    context: PermissionContext | None = None,
) -> int:
    """检测是否满足金币条件

    参数:
        user: UserConsole | None
        plugin: PluginInfo
        session: Uninfo

    返回:
        int: 需要消耗的金币
    """
    start_time = time.time()

    try:
        if context is not None and user is None:
            user = context.user
        user_gold = user.gold if user else DEFAULT_GOLD
        if user_gold < plugin.cost_gold:
            """插件消耗金币不足"""
            raise SkipPluginException(
                f"{plugin.name}({plugin.module}) 金币限制...",
                tip_message=f"金币不足..该功能需要{plugin.cost_gold}金币..",
            )
        return plugin.cost_gold
    finally:
        # 记录执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"auth_cost 耗时: {elapsed:.3f}s, plugin={plugin.module}",
                LOGGER_COMMAND,
                session=session,
            )

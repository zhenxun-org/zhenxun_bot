import time

from nonebot_plugin_uninfo import Uninfo

from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.user_console import UserConsole
from zhenxun.services.log import logger

from .config import LOGGER_COMMAND, WARNING_THRESHOLD
from .exception import SkipPluginException
from .utils import send_message


async def auth_cost(user: UserConsole, plugin: PluginInfo, session: Uninfo) -> int:
    """检测是否满足金币条件

    参数:
        user: UserConsole
        plugin: PluginInfo
        session: Uninfo

    返回:
        int: 需要消耗的金币
    """
    start_time = time.time()

    try:
        if user.gold < plugin.cost_gold:
            """插件消耗金币不足"""
            await send_message(session, f"金币不足..该功能需要{plugin.cost_gold}金币..")
            raise SkipPluginException(f"{plugin.name}({plugin.module}) 金币限制...")
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

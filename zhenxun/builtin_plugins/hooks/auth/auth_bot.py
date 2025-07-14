import asyncio
import time

from zhenxun.models.bot_console import BotConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.data_access import DataAccess
from zhenxun.services.db_context import DB_TIMEOUT_SECONDS
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils

from .config import LOGGER_COMMAND, WARNING_THRESHOLD
from .exception import SkipPluginException


async def auth_bot(plugin: PluginInfo, bot_id: str):
    """bot层面的权限检查

    参数:
        plugin: PluginInfo
        bot_id: bot id

    异常:
        SkipPluginException: 忽略插件
        SkipPluginException: 忽略插件
    """
    start_time = time.time()

    try:
        # 从数据库或缓存中获取 bot 信息
        bot_dao = DataAccess(BotConsole)

        try:
            bot: BotConsole | None = await asyncio.wait_for(
                bot_dao.safe_get_or_none(bot_id=bot_id), timeout=DB_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            logger.error(f"查询Bot信息超时: bot_id={bot_id}", LOGGER_COMMAND)
            # 超时时不阻塞，继续执行
            return

        if not bot or not bot.status:
            raise SkipPluginException("Bot不存在或休眠中阻断权限检测...")
        if CommonUtils.format(plugin.module) in bot.block_plugins:
            raise SkipPluginException(
                f"Bot插件 {plugin.name}({plugin.module}) 权限检查结果为关闭..."
            )
    finally:
        # 记录执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"auth_bot 耗时: {elapsed:.3f}s, "
                f"bot_id={bot_id}, plugin={plugin.module}",
                LOGGER_COMMAND,
            )

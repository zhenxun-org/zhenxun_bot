import time

from zhenxun.models.bot_console import BotConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.cache.runtime_cache import BotMemoryCache, BotSnapshot
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils

from .config import LOGGER_COMMAND, WARNING_THRESHOLD
from .exception import SkipPluginException


async def auth_bot(
    plugin: PluginInfo,
    bot_id: str,
    bot_data: BotConsole | BotSnapshot | None = None,
    skip_fetch: bool = False,
    allow_sleep_bypass: bool = False,
):
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
        bot: BotConsole | BotSnapshot | None = bot_data
        if bot is None and not skip_fetch:
            bot = await BotMemoryCache.get(bot_id)

        if bot is None:
            raise SkipPluginException("Bot不存在，阻断权限检测...")

        if not bot.status and not allow_sleep_bypass:
            raise SkipPluginException("Bot休眠中阻断权限检测...")

        if CommonUtils.format(plugin.module) in bot.block_plugins:
            raise SkipPluginException(
                f"Bot插件 {plugin.name}({plugin.module}) 权限检查结果为关闭..."
            )
    finally:
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:
            logger.warning(
                f"auth_bot 耗时: {elapsed:.3f}s, "
                f"bot_id={bot_id}, plugin={plugin.module}",
                LOGGER_COMMAND,
            )

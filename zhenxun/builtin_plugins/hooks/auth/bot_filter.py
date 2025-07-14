import nonebot
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config

from .exception import SkipPluginException

Config.add_plugin_config(
    "hook",
    "FILTER_BOT",
    True,
    help="过滤当前连接bot（防止bot互相调用）",
    default_value=True,
    type=bool,
)


def bot_filter(session: Uninfo):
    """过滤bot调用bot

    参数:
        session: Uninfo

    异常:
        SkipPluginException: bot互相调用
    """
    if not Config.get_config("hook", "FILTER_BOT"):
        return
    bot_ids = list(nonebot.get_bots().keys())
    if session.user.id == session.self_id:
        return
    if session.user.id in bot_ids:
        raise SkipPluginException(
            f"bot:{session.self_id} 尝试调用 bot:{session.user.id}"
        )

import nonebot
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config

from .context import PermissionContext
from .exception import SkipPluginException

Config.add_plugin_config(
    "hook",
    "FILTER_BOT",
    True,
    help="过滤当前连接bot（防止bot互相调用）",
    default_value=True,
    type=bool,
)


def bot_filter(
    session: Uninfo,
    *,
    context: PermissionContext | None = None,
    user_id: str | None = None,
):
    """过滤bot调用bot

    参数:
        session: Uninfo

    异常:
        SkipPluginException: bot互相调用
    """
    if not Config.get_config("hook", "FILTER_BOT"):
        return
    if context is not None:
        user_id = context.user_id
    bot_ids = list(nonebot.get_bots().keys())
    checked_user_id = user_id or session.user.id
    if checked_user_id == session.self_id:
        return
    if checked_user_id in bot_ids:
        raise SkipPluginException(
            f"bot:{session.self_id} 尝试调用 bot:{checked_user_id}"
        )

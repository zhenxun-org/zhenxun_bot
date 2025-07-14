import time

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot.message import run_postprocessor, run_preprocessor
from nonebot_plugin_alconna import UniMsg
from nonebot_plugin_uninfo import Uninfo

from zhenxun.services.log import logger

from .auth.config import LOGGER_COMMAND
from .auth_checker import LimitManager, auth


# # 权限检测
@run_preprocessor
async def _(matcher: Matcher, event: Event, bot: Bot, session: Uninfo, message: UniMsg):
    start_time = time.time()
    await auth(
        matcher,
        event,
        bot,
        session,
        message,
    )
    logger.debug(f"权限检测耗时：{time.time() - start_time}秒", LOGGER_COMMAND)


# 解除命令block阻塞
@run_postprocessor
async def _(matcher: Matcher, session: Uninfo):
    user_id = session.user.id
    group_id = None
    channel_id = None
    if session.group:
        if session.group.parent:
            group_id = session.group.parent.id
            channel_id = session.group.id
        else:
            group_id = session.group.id
    if user_id and matcher.plugin:
        module = matcher.plugin.name
        LimitManager.unblock(module, user_id, group_id, channel_id)

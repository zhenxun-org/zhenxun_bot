from collections import defaultdict
import time

from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import run_preprocessor
from nonebot.typing import T_State
from nonebot_plugin_alconna import At
from nonebot_plugin_session import EventSession

from zhenxun.configs.config import Config
from zhenxun.models.ban_console import BanConsole
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

malicious_check_time = Config.get_config("hook", "MALICIOUS_CHECK_TIME")
malicious_ban_count = Config.get_config("hook", "MALICIOUS_BAN_COUNT")

if not malicious_check_time:
    raise ValueError("模块: [hook], 配置项: [MALICIOUS_CHECK_TIME] 为空或小于0")
if not malicious_ban_count:
    raise ValueError("模块: [hook], 配置项: [MALICIOUS_BAN_COUNT] 为空或小于0")


class BanCheckLimiter:
    """
    恶意命令触发检测
    """

    def __init__(self, default_check_time: float = 5, default_count: int = 4):
        self.mint = defaultdict(int)
        self.mtime = defaultdict(float)
        self.default_check_time = default_check_time
        self.default_count = default_count

    def add(self, key: str | float):
        if self.mint[key] == 1:
            self.mtime[key] = time.time()
        self.mint[key] += 1

    def check(self, key: str | float) -> bool:
        if time.time() - self.mtime[key] > self.default_check_time:
            return self._extracted_from_check_3(key, False)
        if (
            self.mint[key] >= self.default_count
            and time.time() - self.mtime[key] < self.default_check_time
        ):
            return self._extracted_from_check_3(key, True)
        return False

    # TODO Rename this here and in `check`
    def _extracted_from_check_3(self, key, arg1):
        self.mtime[key] = time.time()
        self.mint[key] = 0
        return arg1


_blmt = BanCheckLimiter(
    malicious_check_time,
    malicious_ban_count,
)


# 恶意触发命令检测
@run_preprocessor
async def _(
    matcher: Matcher, bot: Bot, session: EventSession, state: T_State, event: Event
):
    module = None
    if plugin := matcher.plugin:
        module = plugin.module_name
        if not (metadata := plugin.metadata):
            return
        extra = metadata.extra
        if extra.get("plugin_type") in [
            PluginType.HIDDEN,
            PluginType.DEPENDANT,
            PluginType.ADMIN,
            PluginType.SUPERUSER,
        ]:
            return
    if matcher.type == "notice":
        return
    user_id = session.id1
    group_id = session.id3 or session.id2
    malicious_ban_time = Config.get_config("hook", "MALICIOUS_BAN_TIME")
    if not malicious_ban_time:
        raise ValueError("模块: [hook], 配置项: [MALICIOUS_BAN_TIME] 为空或小于0")
    if user_id and module:
        if _blmt.check(f"{user_id}__{module}"):
            await BanConsole.ban(
                user_id,
                group_id,
                9,
                "恶意触发命令检测",
                malicious_ban_time * 60,
                bot.self_id,
            )
            logger.info(
                f"触发了恶意触发检测: {matcher.plugin_name}",
                "HOOK",
                session=session,
            )
            await MessageUtils.build_message(
                [
                    At(flag="user", target=user_id),
                    "检测到恶意触发命令，您将被封禁 30 分钟",
                ]
            ).send()
            logger.debug(
                f"触发了恶意触发检测: {matcher.plugin_name}",
                "HOOK",
                session=session,
            )
            raise IgnoredException("检测到恶意触发命令")
        _blmt.add(f"{user_id}__{module}")

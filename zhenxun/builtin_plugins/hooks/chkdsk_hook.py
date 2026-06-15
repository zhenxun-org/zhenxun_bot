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

from .auth.context import resolve_actor_user_id, resolve_event_group_id


class BanCheckLimiter:
    """
    恶意命令触发检测
    """

    def __init__(self, default_check_time: float = 5, default_count: int = 4):
        self.mint = defaultdict(int)
        self.mtime = defaultdict(float)
        self.default_check_time = default_check_time
        self.default_count = default_count

    def configure(self, check_time: float, count: int) -> None:
        self.default_check_time = check_time
        self.default_count = count

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
    5,
    4,
)


def _get_positive_config(key: str, cast_type: type[int] | type[float]) -> int | float:
    value = Config.get_config("hook", key)
    try:
        parsed_value = cast_type(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"模块: [hook], 配置项: [{key}] 不是有效数字") from e
    if parsed_value <= 0:
        raise ValueError(f"模块: [hook], 配置项: [{key}] 为空或小于0")
    return parsed_value


# 恶意触发命令检测
@run_preprocessor
async def _(
    matcher: Matcher, bot: Bot, session: EventSession, state: T_State, event: Event
):
    # 提前判断 notice 类型，直接跳过
    if matcher.type == "notice":
        return

    # AI 重路由注入的合成事件不计入恶意检测(A6):AI 链路有自己的预算/审批,
    # 不应被人类反垃圾逻辑封禁(此前批量转发误封超级用户的事故根因之一)。
    if getattr(event, "_ai_triggered", False):
        return

    # 提前判断插件类型，跳过不需要检测的插件
    if plugin := matcher.plugin:
        if metadata := plugin.metadata:
            extra = metadata.extra
            if extra.get("plugin_type") in [
                PluginType.HIDDEN,
                PluginType.DEPENDANT,
                PluginType.ADMIN,
                PluginType.SUPERUSER,
            ]:
                return
        module = plugin.module_name
    else:
        return

    user_id = resolve_actor_user_id(event, session.id1)
    group_id = resolve_event_group_id(event, session.id3 or session.id2)
    # 超级用户豁免恶意检测(A6):与权威权限路径保持一致,避免误封管理者。
    if user_id:
        is_superuser = state.get("_zx_is_superuser")
        if not isinstance(is_superuser, bool):
            is_superuser = user_id in bot.config.superusers
        if is_superuser:
            return
    malicious_check_time = float(_get_positive_config("MALICIOUS_CHECK_TIME", float))
    malicious_ban_count = int(_get_positive_config("MALICIOUS_BAN_COUNT", int))
    malicious_ban_time = int(_get_positive_config("MALICIOUS_BAN_TIME", int))
    _blmt.configure(malicious_check_time, malicious_ban_count)
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

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

_MALICIOUS_CHECK_MODES = {"off", "blacklist", "whitelist"}
_EVENT_PLUGIN_DEDUPE_TTL = 30.0
_EVENT_PLUGIN_DEDUPE_MAX = 4096
_event_plugin_seen: dict[str, float] = {}


def _malicious_check_mode() -> str:
    mode = str(Config.get_config("hook", "MALICIOUS_CHECK_MODE") or "off")
    mode = mode.strip().lower()
    return mode if mode in _MALICIOUS_CHECK_MODES else "off"


def _malicious_plugin_set() -> set[str]:
    value = Config.get_config("hook", "MALICIOUS_CHECK_PLUGINS")
    if value is None:
        return set()
    if isinstance(value, str):
        items = value.replace("\n", ",").split(",")
    elif isinstance(value, list | tuple | set):
        items = value
    else:
        items = [value]
    return {str(item).strip().casefold() for item in items if str(item).strip()}


def _should_check_plugin(module: str, lane: str) -> bool:
    mode = _malicious_check_mode()
    if mode == "off":
        return False

    normalized_module = str(module or "").strip().casefold()
    if not normalized_module:
        return False

    plugin_set = _malicious_plugin_set()
    in_plugin_set = normalized_module in plugin_set
    is_passive = str(lane or "").startswith("passive_")

    if mode == "blacklist":
        return in_plugin_set
    if is_passive:
        return False
    if mode == "whitelist":
        return not in_plugin_set
    return False


def _event_plugin_key(event: Event, user_id: str, module: str) -> str:
    message_id = getattr(event, "message_id", None) or getattr(event, "id", None)
    if message_id is None:
        message_id = id(event)
    return f"{message_id}:{user_id}:{module}"


def _remember_event_plugin_once(key: str) -> bool:
    now = time.monotonic()
    expires_at = _event_plugin_seen.get(key)
    if expires_at is not None and expires_at > now:
        return False

    _event_plugin_seen[key] = now + _EVENT_PLUGIN_DEDUPE_TTL
    if len(_event_plugin_seen) > _EVENT_PLUGIN_DEDUPE_MAX:
        target_size = _EVENT_PLUGIN_DEDUPE_MAX // 2
        for cache_key, cache_expires_at in list(_event_plugin_seen.items()):
            if cache_expires_at <= now or len(_event_plugin_seen) > target_size:
                _event_plugin_seen.pop(cache_key, None)
            if len(_event_plugin_seen) <= target_size:
                break
    return True


def _mark_event_plugin_checked(
    state: T_State, event: Event, user_id: str, module: str
) -> bool:
    checked = state.setdefault("_zx_malicious_checked_plugins", set())
    if isinstance(checked, set):
        if module in checked:
            return False
        checked.add(module)

    return _remember_event_plugin_once(_event_plugin_key(event, user_id, module))


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

    lane = state.get("_zx_dispatch_lane")
    if not _should_check_plugin(module, lane if isinstance(lane, str) else ""):
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
    else:
        return

    if not _mark_event_plugin_checked(state, event, user_id, module):
        return

    # 只统计通过模式/lane过滤且同事件同插件去重后的有效触发。
    limiter_key = f"{user_id}__{module}"
    malicious_check_time = float(_get_positive_config("MALICIOUS_CHECK_TIME", float))
    malicious_ban_count = int(_get_positive_config("MALICIOUS_BAN_COUNT", int))
    malicious_ban_time = int(_get_positive_config("MALICIOUS_BAN_TIME", int))
    _blmt.configure(malicious_check_time, malicious_ban_count)
    if _blmt.check(limiter_key):
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
    _blmt.add(limiter_key)

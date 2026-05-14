import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass, field
import importlib
import re
import time
from typing import cast
import weakref

from nonebot import get_loaded_plugins
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
import nonebot.message as nb_message
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.user_console import UserConsole
from zhenxun.services.cache.cache_containers import CacheDict
from zhenxun.services.cache.runtime_cache import (
    BotMemoryCache,
    BotSnapshot,
    GroupMemoryCache,
    GroupSnapshot,
    LevelUserMemoryCache,
    LevelUserSnapshot,
    PluginInfoMemoryCache,
)
from zhenxun.services.data_access import DataAccess
from zhenxun.services.log import logger
from zhenxun.services.message_load import is_overloaded
from zhenxun.utils.enum import BlockType, GoldHandle, PluginType
from zhenxun.utils.exception import InsufficientGold
from zhenxun.utils.platform import PlatformUtils

from .auth.auth_admin import auth_admin
from .auth.auth_ban import auth_ban
from .auth.auth_bot import auth_bot
from .auth.auth_cost import auth_cost
from .auth.auth_group import auth_group
from .auth.auth_limit import LimitManager, auth_limit
from .auth.auth_plugin import auth_plugin
from .auth.bot_filter import bot_filter
from .auth.config import LOGGER_COMMAND, WARNING_THRESHOLD
from .auth.context import (
    EVENT_CACHE,
    STATE_PLAIN_TEXT,
    EventContext,
    PermissionContext,
    get_event_context,
    get_permission_side_effect_cache,
    set_route_modules,
    store_permission_context,
)
from .auth.exception import (
    IsSuperuserException,
    PermissionExemption,
    SkipPluginException,
)
from .auth.utils import send_message

AUTH_HOOKS_CONCURRENCY_LIMIT = 5
AUTH_DB_CONCURRENCY_LIMIT = 6
AUTH_DISPATCH_COMMAND_EXACT_LIMIT = 96
AUTH_DISPATCH_COMMAND_SHORTCUT_LIMIT = 32
AUTH_DISPATCH_COMMAND_REGEX_LIMIT = 8
AUTH_DISPATCH_SYSTEM_LIMIT = 64
AUTH_DISPATCH_PASSIVE_LIGHT_LIMIT = 12
AUTH_DISPATCH_PASSIVE_DB_LIMIT = 4
AUTH_DISPATCH_PASSIVE_HTTP_LIMIT = 4
AUTH_DISPATCH_PASSIVE_AI_LIMIT = 2
AUTH_DISPATCH_PASSIVE_RENDER_LIMIT = 2


# 超时设置（秒）
TIMEOUT_SECONDS = 5.0
# 熔断计数器
CIRCUIT_BREAKERS = {
    "auth_ban": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_bot": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_group": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_admin": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_plugin": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_limit": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
}
# 熔断重置时间（秒）
CIRCUIT_RESET_TIME = 300  # 5分钟

# 并发控制：限制同时进入 hooks 并行检查的协程数
HOOKS_CONCURRENCY_LIMIT = AUTH_HOOKS_CONCURRENCY_LIMIT
DB_CONCURRENCY_LIMIT = AUTH_DB_CONCURRENCY_LIMIT

# 路由索引缓存
_ROUTE_INDEX_LOCK = asyncio.Lock()
_ROUTE_INDEX_READY = False
_ROUTE_COMMAND_MAP: dict[str, set[str]] = {}
_ROUTE_PREFIX_MAP: dict[str, set[str]] = {}
_ROUTE_MODULES_WITH_COMMANDS: set[str] = set()
MATCHER_ROUTE_PREFILTER_TTL = 2
PREFILTER_STATS_LOG_INTERVAL = 10.0
CACHE_SWEEP_INTERVAL = 1.0
DISPATCH_STATS_LOG_INTERVAL = 10.0

# 全局信号量与计数器
HOOKS_ACTIVE_COUNT = 0
HOOKS_SEMAPHORE = asyncio.Semaphore(HOOKS_CONCURRENCY_LIMIT)

DB_SEMAPHORE = asyncio.Semaphore(DB_CONCURRENCY_LIMIT)
DB_ACTIVE_COUNT = 0
_DISPATCH_LANE_LIMITS: dict[str, int] = {
    "command_exact": AUTH_DISPATCH_COMMAND_EXACT_LIMIT,
    "command_shortcut": AUTH_DISPATCH_COMMAND_SHORTCUT_LIMIT,
    "command_regex": AUTH_DISPATCH_COMMAND_REGEX_LIMIT,
    "system": AUTH_DISPATCH_SYSTEM_LIMIT,
    "passive_light": AUTH_DISPATCH_PASSIVE_LIGHT_LIMIT,
    "passive_db": AUTH_DISPATCH_PASSIVE_DB_LIMIT,
    "passive_http": AUTH_DISPATCH_PASSIVE_HTTP_LIMIT,
    "passive_ai": AUTH_DISPATCH_PASSIVE_AI_LIMIT,
    "passive_render": AUTH_DISPATCH_PASSIVE_RENDER_LIMIT,
}
_DISPATCH_LANE_SEMAPHORES = {
    lane: asyncio.Semaphore(limit)
    for lane, limit in _DISPATCH_LANE_LIMITS.items()
    if limit > 0
}
_CHECK_MATCHER_PATCHED = False
_ORIGINAL_CHECK_AND_RUN_MATCHER: Callable[..., Awaitable[None]] | None = None
_HANDLE_EVENT_PATCHED = False
_ORIGINAL_HANDLE_EVENT: Callable[..., Awaitable[None]] | None = None
_ORIGINAL_ADAPTER_HANDLE_EVENTS: dict[object, object] = {}
_MATCHER_COMMAND_TYPE_CACHE: dict[type[Matcher], bool] = {}
_MATCHER_COMMAND_LITERAL_CACHE: dict[type[Matcher], tuple[str, ...] | None] = {}
_MATCHER_ALCONNA_SHORTCUT_CACHE: dict[type[Matcher], tuple[str, ...] | None] = {}
_CHECK_MATCHER_ROUTE_CACHE = CacheDict(
    "AUTH_MATCHER_ROUTE_CACHE", expire=MATCHER_ROUTE_PREFILTER_TTL
)
_PREFILTER_STATS = {
    "checked": 0,
    "skipped": 0,
    "before_task_checked": 0,
    "before_task_skipped": 0,
    "inside_task_checked": 0,
    "inside_task_skipped": 0,
    "type_miss": 0,
    "route_miss": 0,
    "command_miss": 0,
    "empty_text": 0,
}
_PREFILTER_LAST_LOG = 0.0
_DISPATCH_SELECTED = 0
_DISPATCH_SKIPPED = 0
_DISPATCH_SELECTED_BY_LANE: dict[str, int] = {
    "command_exact": 0,
    "command_shortcut": 0,
    "command_regex": 0,
    "system": 0,
    "passive_light": 0,
    "passive_db": 0,
    "passive_http": 0,
    "passive_ai": 0,
    "passive_render": 0,
}
_DISPATCH_SKIPPED_BY_LANE: dict[str, int] = {
    "command_exact": 0,
    "command_shortcut": 0,
    "command_regex": 0,
    "system": 0,
    "passive_light": 0,
    "passive_db": 0,
    "passive_http": 0,
    "passive_ai": 0,
    "passive_render": 0,
}
_DISPATCH_LANE_WAIT_MS: dict[str, float] = {
    "command_exact": 0.0,
    "command_shortcut": 0.0,
    "command_regex": 0.0,
    "system": 0.0,
    "passive_light": 0.0,
    "passive_db": 0.0,
    "passive_http": 0.0,
    "passive_ai": 0.0,
    "passive_render": 0.0,
}
_DISPATCH_LAST_LOG = 0.0
_CACHE_SWEEP_TASK: asyncio.Task | None = None
_BOT_WAKE_COMMAND_PATTERN = re.compile(r"^bot醒来(?:\s+\S+)?$", re.IGNORECASE)
_BOT_WAKE_CANONICAL_PATTERN = re.compile(
    r"^bot_manage\s+bot_switch\s+enable(?:\s+\S+)?$", re.IGNORECASE
)
_URL_PATTERN = re.compile(r"(?:https?://|www\.|b23\.tv|t\.cn/)", re.IGNORECASE)
_PASSIVE_DB_HINTS = (
    "word_bank",
    "black_word",
    "history",
    "statistics",
    "sign",
    "gold",
    "redbag",
    "mute",
    "group",
    "user",
    "admin",
    "ban",
    "limit",
    "check",
)
_PASSIVE_HTTP_HINTS = (
    "http",
    "translate",
    "bilibili",
    "music",
    "comment",
    "nbnhhsh",
    "quote",
    "search",
    "jitang",
    "poetry",
    "anime",
    "cover",
)
_PASSIVE_AI_HINTS = (
    "chatinter",
    "dialogue",
    "ai",
    "llm",
    "fudu",
    "bym_ai",
)
_PASSIVE_RENDER_HINTS = (
    "render",
    "image",
    "meme",
    "memes",
    "word_cloud",
    "wordcloud",
    "pic",
    "picture",
    "coser",
    "luxun",
)


@dataclass(slots=True)
class EventDispatchContext:
    event_type: str
    plain_text: str = ""
    raw_text: str = ""
    to_me: bool = False
    has_url: bool = False
    has_image: bool = False
    is_command_like: bool = False
    route_modules: set[str] = field(default_factory=set)
    ai_route_modules: set[str] = field(default_factory=set)
    ai_route_heads: set[str] = field(default_factory=set)


class HookTraceRecorder:
    def __init__(self, start_time: float) -> None:
        self._start_time = start_time
        self._enabled = False
        self._data: dict[str, str] = {}

    def _ensure_enabled(self) -> bool:
        if self._enabled:
            return True
        if time.time() - self._start_time <= WARNING_THRESHOLD:
            return False
        self._enabled = True
        return True

    def set(self, key: str, value: str) -> None:
        if self._ensure_enabled():
            self._data[key] = value

    def setdefault(self, key: str, value: str) -> None:
        if self._ensure_enabled():
            self._data.setdefault(key, value)

    def contains(self, key: str) -> bool:
        return key in self._data

    def snapshot(self) -> dict[str, str]:
        return self._data if self._enabled else {}


def _debug_log(message: str, *args, **kwargs) -> None:
    if is_overloaded():
        return
    logger.debug(message, *args, **kwargs)


def _normalize_command(command: str) -> str:
    text = command.strip()
    if not text:
        return ""

    # strip leading placeholders like "[引用消息] 撤回"
    text = re.sub(r"^(?:\s*(?:\[[^\]]*]|\<[^>]*>))+\s*", "", text)

    # keep command head: "点歌 [歌名]" -> "点歌", "foo <arg>" -> "foo"
    cut_points = [idx for idx in (text.find("["), text.find("<")) if idx >= 0]
    if cut_points:
        text = text[: min(cut_points)]

    # normalize spacing after trimming placeholders
    text = re.sub(r"\s+", " ", text).strip()
    # remove trailing template markers left by forms like "foo ?[arg]" / "foo ?*[tags]"
    text = re.sub(r"(?:\s+[?*]+|[?*]+)$", "", text).strip()
    return text


def _is_bot_wake_command(module: str, text: str | None) -> bool:
    if "bot_manage" not in (module or ""):
        return False
    if not text:
        return False
    normalized = re.sub(r"\s+", " ", text.strip())
    if not normalized:
        return False
    return (
        _BOT_WAKE_COMMAND_PATTERN.match(normalized) is not None
        or _BOT_WAKE_CANONICAL_PATTERN.match(normalized) is not None
    )


def _split_command_variants(command: str) -> tuple[str, ...]:
    text = command.strip()
    if not text:
        return ()
    # Keep slash-prefixed commands like "/info" as-is.
    if text.startswith("/"):
        return (text,)
    # "今日运势/抽签/运势" => ("今日运势", "抽签", "运势")
    if "/" in text and " " not in text:
        parts = tuple(part.strip() for part in text.split("/") if part.strip())
        if parts:
            return parts
    return (text,)


def _is_ambiguous_route_command(command: str) -> bool:
    text = command.strip()
    if not text:
        return True
    # Keep route-index strict only for literal, deterministic command heads.
    if any(token in text for token in ("?", "*", "|", "(", ")", "^", "$", "re:")):
        return True
    if "xx" in text.lower():
        return True
    return False


def _extract_commands(extra: PluginExtraData | None) -> tuple[set[str], bool]:
    if not extra:
        return set(), False
    commands = {c.command for c in extra.commands if c.command}
    commands.update(extra.aliases or set())
    normalized_commands: set[str] = set()
    has_ambiguous = False
    for command in commands:
        normalized = _normalize_command(command)
        if not normalized:
            continue
        for variant in _split_command_variants(normalized):
            if _is_ambiguous_route_command(variant):
                has_ambiguous = True
                continue
            normalized_commands.add(variant)
    return normalized_commands, has_ambiguous


async def _ensure_route_index():
    global _ROUTE_INDEX_READY
    if _ROUTE_INDEX_READY:
        return
    async with _ROUTE_INDEX_LOCK:
        if _ROUTE_INDEX_READY:
            return
        _ROUTE_COMMAND_MAP.clear()
        _ROUTE_PREFIX_MAP.clear()
        _ROUTE_MODULES_WITH_COMMANDS.clear()
        for plugin in get_loaded_plugins():
            if not plugin.metadata:
                continue
            extra = plugin.metadata.extra or {}
            try:
                extra_data = PluginExtraData(**extra)
            except Exception:
                continue
            command_set, has_ambiguous = _extract_commands(extra_data)
            if not command_set:
                continue
            if has_ambiguous:
                continue
            module = plugin.name
            _ROUTE_MODULES_WITH_COMMANDS.add(module)
            module_name = getattr(plugin, "module_name", None) or ""
            if module_name and module_name != module:
                _ROUTE_MODULES_WITH_COMMANDS.add(module_name)
            for normalized in command_set:
                _ROUTE_COMMAND_MAP.setdefault(normalized, set()).add(module)
                _ROUTE_PREFIX_MAP.setdefault(normalized[0], set()).add(normalized)
        _ROUTE_INDEX_READY = True


def _command_matches(text: str, command: str) -> bool:
    if not text or not command:
        return False
    if text == command:
        return True
    if text.startswith(command):
        if len(text) == len(command):
            return True
        next_char = text[len(command)]
        return next_char.isspace()
    return False


def _matcher_command_matches(text: str, command: str) -> bool:
    normalized = command.strip()
    if not normalized:
        return False
    if normalized.startswith("re:"):
        pattern = normalized.removeprefix("re:").strip()
        if not pattern:
            return False
        try:
            return re.search(pattern, text) is not None
        except re.error:
            return False
    if _command_matches(text, normalized):
        return True
    # CJK command heads often accept compact arguments, e.g. "鲁迅说测试".
    return text.startswith(normalized) and not normalized[-1].isascii()


def _is_regex_like_command_literal(command: str) -> bool:
    text = command.strip()
    if not text:
        return False
    if text.startswith("re:"):
        return True
    return any(token in text for token in ("\\", "(", ")", "[", "]", "|", "^", "$"))


def _match_route_modules(text: str) -> set[str]:
    text = text.strip()
    if not text:
        return set()
    commands = _ROUTE_PREFIX_MAP.get(text[0])
    if not commands:
        return set()
    matched_modules: set[str] = set()
    for command in commands:
        if _command_matches(text, command):
            modules = _ROUTE_COMMAND_MAP.get(command)
            if modules:
                matched_modules.update(modules)
    return matched_modules


def _matcher_module_name(matcher_cls: type[Matcher]) -> str:
    module = getattr(matcher_cls, "plugin_name", "") or ""
    if module:
        return module
    plugin = getattr(matcher_cls, "plugin", None)
    if not plugin:
        return ""
    return (getattr(plugin, "name", "") or "").strip()


def _collect_ai_route_modules(event: Event, state: dict | None = None) -> set[str]:
    if state is not None:
        cached = state.get("_zx_ai_route_modules")
        if isinstance(cached, set):
            return cached

    raw_value = getattr(event, "_ai_route_modules", None)
    result: set[str] = set()
    if isinstance(raw_value, str):
        normalized = raw_value.strip()
        if normalized:
            result.add(normalized)
    elif isinstance(raw_value, set | frozenset | list | tuple):
        for item in raw_value:
            if not isinstance(item, str):
                continue
            normalized = item.strip()
            if normalized:
                result.add(normalized)

    if state is not None and result:
        state["_zx_ai_route_modules"] = result
    return result


def _collect_ai_route_heads(event: Event, state: dict | None = None) -> set[str]:
    if state is not None:
        cached = state.get("_zx_ai_route_heads")
        if isinstance(cached, set):
            return cached

    raw_value = getattr(event, "_ai_route_heads", None)
    result: set[str] = set()
    if isinstance(raw_value, str):
        normalized = raw_value.strip().casefold()
        if normalized:
            result.add(normalized)
    elif isinstance(raw_value, set | frozenset | list | tuple):
        for item in raw_value:
            if not isinstance(item, str):
                continue
            normalized = item.strip().casefold()
            if normalized:
                result.add(normalized)

    if state is not None and result:
        state["_zx_ai_route_heads"] = result
    return result


def _matcher_matches_ai_route_heads(
    matcher_cls: type[Matcher],
    ai_route_heads: set[str],
) -> bool:
    if not ai_route_heads:
        return False
    matcher_commands = _extract_matcher_command_literals(matcher_cls)
    for command in matcher_commands or ():
        normalized_command = command.strip().casefold()
        if not normalized_command:
            continue
        for head in ai_route_heads:
            if not head:
                continue
            if _matcher_command_matches(head, normalized_command) or _command_matches(
                normalized_command, head
            ):
                return True
    shortcuts = _extract_matcher_alconna_shortcuts(matcher_cls) or ()
    for shortcut in shortcuts:
        for head in ai_route_heads:
            if head and _shortcut_matches_text(head, shortcut):
                return True
    return False


def _is_command_matcher_class(matcher_cls: type[Matcher]) -> bool:
    if matcher_cls in _MATCHER_COMMAND_TYPE_CACHE:
        return _MATCHER_COMMAND_TYPE_CACHE[matcher_cls]
    if hasattr(matcher_cls, "command"):
        _MATCHER_COMMAND_TYPE_CACHE[matcher_cls] = True
        return True
    rule = getattr(matcher_cls, "rule", None)
    checkers = getattr(rule, "checkers", ()) or ()
    for checker in checkers:
        call = getattr(checker, "call", None)
        if call is None:
            continue
        call_type = call.__class__
        call_module = getattr(call_type, "__module__", "")
        call_name = getattr(call_type, "__name__", "")
        if call_module.startswith("nonebot.rule") and call_name in {
            "CommandRule",
            "ShellCommandRule",
            "Command",
            "ShellCommand",
        }:
            _MATCHER_COMMAND_TYPE_CACHE[matcher_cls] = True
            return True
        if (
            call_module.startswith("nonebot_plugin_alconna.rule")
            and call_name == "AlconnaRule"
        ):
            _MATCHER_COMMAND_TYPE_CACHE[matcher_cls] = True
            return True
    _MATCHER_COMMAND_TYPE_CACHE[matcher_cls] = False
    return False


def _matcher_route_cache_key(event: Event) -> str:
    msg_id = getattr(event, "message_id", None)
    if msg_id is None:
        msg_id = getattr(event, "id", None)
    if msg_id is None:
        msg_id = id(event)
    user_id = getattr(event, "user_id", "")
    group_id = getattr(event, "group_id", "")
    channel_id = getattr(event, "channel_id", "")
    return f"{msg_id}:{user_id}:{group_id}:{channel_id}"


def _event_plain_text(event: Event) -> str:
    def _normalize(text: str) -> str:
        normalized = text.strip()
        if not normalized:
            return ""
        # strip leading placeholders like "[reply:id=10004]撤回"
        normalized = re.sub(
            r"^(?:\s*(?:\[[^\]]*]|\<[^>]*>))+\s*",
            "",
            normalized,
        )
        return normalized.strip()

    with contextlib.suppress(Exception):
        # Use raw_message if available (OneBot v11) to get the original text
        # before nickname stripping. This ensures command matching works correctly
        # for commands like "真寻日报" when "真寻" is a bot nickname.
        raw = getattr(event, "raw_message", None)
        if isinstance(raw, str) and raw:
            return _normalize(raw)
        return _normalize(event.get_plaintext() or "")
    return ""


def _state_plain_text(state: dict | None) -> str:
    if state is None:
        return ""
    context = get_event_context(state)
    if context is not None:
        return context.plain_text.strip()
    text = state.get("_zx_plain_text")
    if isinstance(text, str):
        return text.strip()
    return ""


def _event_raw_message_text(event: Event) -> str:
    with contextlib.suppress(Exception):
        message = getattr(event, "message", None)
        if message is not None:
            return str(message)
    return ""


def _event_has_image(event: Event) -> bool:
    text = _event_raw_message_text(event)
    lowered = text.casefold()
    return "[cq:image" in lowered or "[image:" in lowered


def _event_has_url(text: str) -> bool:
    return bool(_URL_PATTERN.search(text))


def _event_to_me(event: Event) -> bool:
    with contextlib.suppress(Exception):
        getter = getattr(event, "is_tome", None)
        if callable(getter):
            return bool(getter())
    return bool(getattr(event, "to_me", False))


def _context_from_state(state: dict | None) -> EventDispatchContext | None:
    if state is None:
        return None
    context = state.get("_zx_dispatch_context")
    return context if isinstance(context, EventDispatchContext) else None


def _build_dispatch_context_sync(
    event: Event, state: dict | None = None
) -> EventDispatchContext:
    context = _context_from_state(state)
    if context is not None:
        return context

    event_type = event.get_type()
    plain_text = _state_plain_text(state)
    if not plain_text:
        plain_text = _event_plain_text(event)
        if state is not None and plain_text:
            state["_zx_plain_text"] = plain_text

    route_modules = (
        _get_route_modules_for_event(event, state) if _ROUTE_INDEX_READY else set()
    )
    ai_route_modules = _collect_ai_route_modules(event, state)
    ai_route_heads = _collect_ai_route_heads(event, state)
    raw_text = _event_raw_message_text(event)
    to_me = _event_to_me(event)
    has_url = _event_has_url(raw_text) or _event_has_url(plain_text)
    has_image = _event_has_image(event)
    is_command_like = bool(
        route_modules
        or ai_route_modules
        or plain_text.startswith("/")
        or plain_text.startswith("!")
        or plain_text.startswith(".")
    )
    context = EventDispatchContext(
        event_type=event_type,
        plain_text=plain_text,
        raw_text=raw_text,
        to_me=to_me,
        has_url=has_url,
        has_image=has_image,
        is_command_like=is_command_like,
        route_modules=route_modules,
        ai_route_modules=ai_route_modules,
        ai_route_heads=ai_route_heads,
    )
    if state is not None:
        state["_zx_dispatch_context"] = context
    return context


async def _build_dispatch_context(
    event: Event, state: dict | None = None
) -> EventDispatchContext:
    context = _build_dispatch_context_sync(event, state)
    await _ensure_route_index()
    if not context.route_modules:
        route_modules = _get_route_modules_for_event(event, state)
        context.route_modules = route_modules
        context.is_command_like = bool(
            route_modules
            or context.ai_route_modules
            or context.plain_text.startswith("/")
            or context.plain_text.startswith("!")
            or context.plain_text.startswith(".")
        )
    return context


def _dispatch_lane_for_matcher(
    matcher_cls: type[Matcher], context: EventDispatchContext
) -> str:
    event_type = context.event_type
    if getattr(matcher_cls, "temp", False):
        return "system"
    matcher_type = getattr(matcher_cls, "type", "") or ""
    if isinstance(matcher_type, str) and matcher_type and matcher_type != event_type:
        return "system"
    if _is_command_matcher_class(matcher_cls):
        return _dispatch_command_lane_for_matcher(matcher_cls)

    module = _matcher_module_name(matcher_cls).casefold()
    if not module:
        return "passive_light"
    if any(
        module == route_module.casefold() for route_module in context.ai_route_modules
    ):
        return "passive_ai"
    if any(hint in module for hint in _PASSIVE_AI_HINTS):
        return "passive_ai"
    if any(hint in module for hint in _PASSIVE_RENDER_HINTS):
        return "passive_render"
    if any(hint in module for hint in _PASSIVE_HTTP_HINTS):
        return "passive_http"
    if any(hint in module for hint in _PASSIVE_DB_HINTS):
        return "passive_db"
    return "passive_light"


def _dispatch_command_lane_for_matcher(matcher_cls: type[Matcher]) -> str:
    if _matcher_has_alconna_shortcuts(matcher_cls):
        return "command_shortcut"
    commands = _extract_matcher_command_literals(matcher_cls) or ()
    if any(_is_regex_like_command_literal(command) for command in commands):
        return "command_regex"
    return "command_exact"


def _dispatch_budget_for_context(context: EventDispatchContext) -> dict[str, int]:
    budget = {
        "passive_light": AUTH_DISPATCH_PASSIVE_LIGHT_LIMIT,
        "passive_db": AUTH_DISPATCH_PASSIVE_DB_LIMIT,
        "passive_http": AUTH_DISPATCH_PASSIVE_HTTP_LIMIT if context.has_url else 0,
        "passive_ai": AUTH_DISPATCH_PASSIVE_AI_LIMIT
        if (context.to_me or context.is_command_like)
        else int(bool(context.plain_text)),
        "passive_render": AUTH_DISPATCH_PASSIVE_RENDER_LIMIT
        if (context.to_me or context.has_image or context.is_command_like)
        else 0,
    }
    return budget


def _record_dispatch_selection(lane: str, selected: bool, wait_ms: float = 0.0) -> None:
    global _DISPATCH_LAST_LOG, _DISPATCH_SELECTED, _DISPATCH_SKIPPED
    if lane == "command":
        lane = "command_exact"
    lane = lane if lane in _DISPATCH_SELECTED_BY_LANE else "passive_light"
    if selected:
        _DISPATCH_SELECTED += 1
        _DISPATCH_SELECTED_BY_LANE[lane] += 1
        _DISPATCH_LANE_WAIT_MS[lane] += wait_ms
    else:
        _DISPATCH_SKIPPED += 1
        _DISPATCH_SKIPPED_BY_LANE[lane] += 1

    now = time.monotonic()
    if now - _DISPATCH_LAST_LOG < DISPATCH_STATS_LOG_INTERVAL or is_overloaded():
        return
    _DISPATCH_LAST_LOG = now
    wait_snapshot = {
        lane: round(wait, 2) for lane, wait in _DISPATCH_LANE_WAIT_MS.items()
    }
    lane_snapshot = " ".join(
        f"{lane}={count}" for lane, count in _DISPATCH_SELECTED_BY_LANE.items()
    )
    _debug_log(
        (
            "dispatch stats: "
            f"selected={_DISPATCH_SELECTED} "
            f"skipped={_DISPATCH_SKIPPED} "
            f"{lane_snapshot} "
            f"wait_ms={wait_snapshot}"
        ),
        LOGGER_COMMAND,
    )


def _passive_signal_skip_reason(lane: str, context: EventDispatchContext) -> str | None:
    if context.event_type != "message":
        return None
    if lane == "passive_ai" and not (
        context.to_me or context.is_command_like or context.plain_text
    ):
        return "passive_ai_no_signal"
    if lane == "passive_http" and not (context.has_url or context.is_command_like):
        return "passive_http_no_signal"
    if lane == "passive_render" and not (
        context.to_me or context.has_image or context.is_command_like
    ):
        return "passive_render_no_signal"
    if lane in {"passive_light", "passive_db"} and not context.plain_text:
        return "empty_text"
    return None


def _consume_dispatch_budget(
    lane: str,
    budget: dict[str, int],
    *,
    ignore: bool = False,
) -> bool:
    if ignore or lane not in budget:
        return True
    if budget[lane] <= 0:
        return False
    budget[lane] -= 1
    return True


@contextlib.asynccontextmanager
async def _dispatch_lane_section(lane: str):
    semaphore = _DISPATCH_LANE_SEMAPHORES.get(lane)
    if semaphore is None:
        yield
        return
    started = time.perf_counter()
    await semaphore.acquire()
    wait_ms = (time.perf_counter() - started) * 1000
    _record_dispatch_selection(lane, True, wait_ms=wait_ms)
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            semaphore.release()


def get_dispatch_snapshot() -> dict[str, object]:
    lane_active = {}
    for lane, semaphore in _DISPATCH_LANE_SEMAPHORES.items():
        limit = _DISPATCH_LANE_LIMITS.get(lane, 0)
        value = getattr(semaphore, "_value", limit)
        lane_active[lane] = max(limit - int(value), 0)
    return {
        "selected": _DISPATCH_SELECTED,
        "skipped": _DISPATCH_SKIPPED,
        "selected_by_lane": dict(_DISPATCH_SELECTED_BY_LANE),
        "skipped_by_lane": dict(_DISPATCH_SKIPPED_BY_LANE),
        "lane_wait_ms": dict(_DISPATCH_LANE_WAIT_MS),
        "lane_active": lane_active,
        "lane_limits": dict(_DISPATCH_LANE_LIMITS),
    }


def _get_route_modules_for_event(event: Event, state: dict | None = None) -> set[str]:
    if state is not None:
        context = get_event_context(state)
        if context is not None and context.route_modules_loaded:
            return context.route_modules
        route_modules = state.get("_zx_route_modules")
        if isinstance(route_modules, set):
            return route_modules
    key = _matcher_route_cache_key(event)
    try:
        route_modules = _CHECK_MATCHER_ROUTE_CACHE[key]
    except KeyError:
        route_modules = _match_route_modules(_event_plain_text(event))
        _CHECK_MATCHER_ROUTE_CACHE[key] = route_modules
    if state is not None:
        context = get_event_context(state)
        if context is not None:
            set_route_modules(state, context, route_modules)
        else:
            state["_zx_route_modules"] = route_modules
    return route_modules


def _prepare_handle_event_state(event: Event, state: dict) -> None:
    get_permission_side_effect_cache(state=state)
    if event.get_type() != "message":
        return
    if _state_plain_text(state):
        return
    text = _event_plain_text(event)
    if text:
        state[STATE_PLAIN_TEXT] = text


def _build_matcher_state(base_state: dict) -> dict:
    get_permission_side_effect_cache(state=base_state)
    matcher_state = base_state.copy()
    get_permission_side_effect_cache(state=matcher_state)
    return matcher_state


async def _run_selected_matcher(
    matcher: type[Matcher],
    bot: Bot,
    event: Event,
    state: dict,
    stack,
    dependency_cache,
    lane: str = "command_exact",
) -> None:
    async with _dispatch_lane_section(lane):
        await nb_message.check_and_run_matcher(
            matcher,
            bot,
            event,
            state,
            stack,
            dependency_cache,
        )


def _record_prefilter_stats(
    skipped: bool,
    reason: str | None,
    stage: str = "inside_task",
) -> None:
    global _PREFILTER_LAST_LOG
    _PREFILTER_STATS["checked"] += 1
    if skipped:
        _PREFILTER_STATS["skipped"] += 1
    if stage == "before_task":
        _PREFILTER_STATS["before_task_checked"] += 1
        if skipped:
            _PREFILTER_STATS["before_task_skipped"] += 1
    else:
        _PREFILTER_STATS["inside_task_checked"] += 1
        if skipped:
            _PREFILTER_STATS["inside_task_skipped"] += 1
    if reason == "type_miss":
        _PREFILTER_STATS["type_miss"] += 1
    elif reason == "route_miss":
        _PREFILTER_STATS["route_miss"] += 1
    elif reason == "command_miss":
        _PREFILTER_STATS["command_miss"] += 1
    elif reason == "empty_text":
        _PREFILTER_STATS["empty_text"] += 1

    if _PREFILTER_STATS["checked"] % 1024 == 0:
        with contextlib.suppress(Exception):
            _ = len(_CHECK_MATCHER_ROUTE_CACHE)

    now = time.monotonic()
    if now - _PREFILTER_LAST_LOG < PREFILTER_STATS_LOG_INTERVAL or is_overloaded():
        return
    _PREFILTER_LAST_LOG = now
    _debug_log(
        (
            "matcher prefilter stats: "
            f"checked={_PREFILTER_STATS['checked']} "
            f"skipped={_PREFILTER_STATS['skipped']} "
            f"before_task={_PREFILTER_STATS['before_task_skipped']}/"
            f"{_PREFILTER_STATS['before_task_checked']} "
            f"inside_task={_PREFILTER_STATS['inside_task_skipped']}/"
            f"{_PREFILTER_STATS['inside_task_checked']} "
            f"type_miss={_PREFILTER_STATS['type_miss']} "
            f"route_miss={_PREFILTER_STATS['route_miss']} "
            f"command_miss={_PREFILTER_STATS['command_miss']} "
            f"empty_text={_PREFILTER_STATS['empty_text']}"
        ),
        LOGGER_COMMAND,
    )


def _collect_command_literals(value, target: set[str], depth: int = 0) -> None:
    if depth > 3 or value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            target.add(text)
        return
    if isinstance(value, weakref.ReferenceType):
        resolved = value()
        if resolved is not None and resolved is not value:
            _collect_command_literals(resolved, target, depth + 1)
        return
    if isinstance(value, list | tuple | set | frozenset):
        for item in value:
            _collect_command_literals(item, target, depth + 1)
        return
    if callable(value) and getattr(value, "__self__", None) is not None:
        with contextlib.suppress(TypeError, RuntimeError, ReferenceError):
            resolved = value()
            if resolved is not None and resolved is not value:
                _collect_command_literals(resolved, target, depth + 1)
                return
    for attr in (
        "name",
        "path",
        "aliases",
        "header_display",
        "command",
        "commands",
        "cmd",
        "cmds",
    ):
        nested = getattr(value, attr, None)
        if nested is not None and nested is not value:
            _collect_command_literals(nested, target, depth + 1)


def _extract_matcher_command_literals(
    matcher_cls: type[Matcher],
) -> tuple[str, ...] | None:
    if matcher_cls in _MATCHER_COMMAND_LITERAL_CACHE:
        return _MATCHER_COMMAND_LITERAL_CACHE[matcher_cls]

    commands: set[str] = set()
    _collect_command_literals(getattr(matcher_cls, "command", None), commands)

    rule = getattr(matcher_cls, "rule", None)
    checkers = getattr(rule, "checkers", ()) or ()
    for checker in checkers:
        call = getattr(checker, "call", None)
        if call is None:
            continue
        for attr in ("cmds", "command", "commands", "cmd"):
            _collect_command_literals(getattr(call, attr, None), commands)

    if not commands:
        _MATCHER_COMMAND_LITERAL_CACHE[matcher_cls] = None
        return None

    sorted_commands = tuple(sorted(commands, key=len, reverse=True))
    _MATCHER_COMMAND_LITERAL_CACHE[matcher_cls] = sorted_commands
    return sorted_commands


def _collect_alconna_shortcut_strings(command) -> set[str]:
    shortcuts: set[str] = set()
    if command is None:
        return shortcuts

    get_shortcuts = getattr(command, "get_shortcuts", None)
    if callable(get_shortcuts):
        with contextlib.suppress(Exception):
            raw_shortcuts = get_shortcuts()
            if isinstance(raw_shortcuts, list | tuple | set | frozenset):
                for shortcut in raw_shortcuts:
                    if isinstance(shortcut, str) and shortcut.strip():
                        shortcuts.add(shortcut.strip())

    formatter = getattr(command, "formatter", None)
    data = getattr(formatter, "data", None)
    if isinstance(data, dict):
        for trace in data.values():
            trace_shortcuts = getattr(trace, "shortcuts", None)
            if not isinstance(trace_shortcuts, dict):
                continue
            for shortcut in trace_shortcuts:
                if isinstance(shortcut, str) and shortcut.strip():
                    shortcuts.add(shortcut.strip())
    return shortcuts


def _extract_matcher_alconna_shortcuts(
    matcher_cls: type[Matcher],
) -> tuple[str, ...] | None:
    if matcher_cls in _MATCHER_ALCONNA_SHORTCUT_CACHE:
        return _MATCHER_ALCONNA_SHORTCUT_CACHE[matcher_cls]

    shortcuts: set[str] = set()
    rule = getattr(matcher_cls, "rule", None)
    checkers = getattr(rule, "checkers", ()) or ()
    for checker in checkers:
        call = getattr(checker, "call", None)
        if call is None:
            continue
        call_type = call.__class__
        call_module = getattr(call_type, "__module__", "")
        call_name = getattr(call_type, "__name__", "")
        if not (
            call_module.startswith("nonebot_plugin_alconna.rule")
            and call_name == "AlconnaRule"
        ):
            continue

        command_ref = getattr(call, "command", None)
        command = None
        if callable(command_ref):
            with contextlib.suppress(Exception):
                command = command_ref()
        shortcuts.update(_collect_alconna_shortcut_strings(command))

    if not shortcuts:
        _MATCHER_ALCONNA_SHORTCUT_CACHE[matcher_cls] = None
        return None

    result = tuple(sorted(shortcuts, key=len, reverse=True))
    _MATCHER_ALCONNA_SHORTCUT_CACHE[matcher_cls] = result
    return result


def _matcher_has_alconna_shortcuts(matcher_cls: type[Matcher]) -> bool:
    return bool(_extract_matcher_alconna_shortcuts(matcher_cls))


def _normalize_shortcut_pattern(shortcut: str) -> str:
    text = shortcut.strip()
    if not text:
        return ""
    text = re.sub(r"^\[\]\s*", "", text)
    text = re.sub(r"\s*\.\.\.args?$", "", text).strip()
    text = re.sub(r"\s*\.\.\.$", "", text).strip()
    return text


def _is_regex_like_shortcut(pattern: str) -> bool:
    return any(token in pattern for token in ("\\", "(", ")", "[", "]", "|", "^", "$"))


def _placeholder_shortcut_matches(text: str, pattern: str) -> bool:
    if "{" not in pattern or "}" not in pattern:
        return False
    pieces: list[str] = []
    last = 0
    for match in re.finditer(r"\{[^{}]+\}", pattern):
        pieces.append(re.escape(pattern[last : match.start()]))
        pieces.append(r"\S+")
        last = match.end()
    if not pieces:
        return False
    pieces.append(re.escape(pattern[last:]))
    try:
        return re.match(rf"^{''.join(pieces)}(?:\s|$)", text) is not None
    except re.error:
        return False


def _shortcut_matches_text(text: str, shortcut: str) -> bool:
    pattern = _normalize_shortcut_pattern(shortcut)
    if not pattern:
        return False
    if _placeholder_shortcut_matches(text, pattern):
        return True
    if _is_regex_like_shortcut(pattern):
        try:
            return re.match(pattern, text) is not None
        except re.error:
            return False
    return _matcher_command_matches(text, pattern)


def _matcher_alconna_shortcut_matches(
    matcher_cls: type[Matcher], text: str
) -> bool | None:
    shortcuts = _extract_matcher_alconna_shortcuts(matcher_cls)
    if shortcuts is None:
        return None
    for shortcut in shortcuts:
        if _shortcut_matches_text(text, shortcut):
            return True
    return False


async def _check_matcher_prefilter(
    matcher_cls: type[Matcher], event: Event, state: dict | None = None
) -> tuple[bool, str | None]:
    dispatch_context = _context_from_state(state) or _build_dispatch_context_sync(
        event, state
    )
    event_type = event.get_type()
    matcher_type = getattr(matcher_cls, "type", "") or ""
    if isinstance(matcher_type, str) and matcher_type and matcher_type != event_type:
        # Explicit matcher type mismatch cannot match this event.
        return True, "type_miss"

    if event_type != "message":
        if _is_command_matcher_class(matcher_cls):
            return True, "non_message_command"
        return False, None

    # Session continuation matchers generated by pause/reject are temp=True.
    # They must bypass command-route prefilter, otherwise follow-up messages
    # (e.g. got_path waiting for plain text) will be dropped.
    if getattr(matcher_cls, "temp", False):
        return False, None

    is_command_matcher = _is_command_matcher_class(matcher_cls)
    if not is_command_matcher:
        lane = _dispatch_lane_for_matcher(matcher_cls, dispatch_context)
        if reason := _passive_signal_skip_reason(lane, dispatch_context):
            return True, reason
        return False, None

    text = dispatch_context.plain_text or _state_plain_text(state)
    if is_command_matcher and not text:
        text = _event_plain_text(event)
        if state is not None and text:
            state["_zx_plain_text"] = text
    if is_command_matcher and not text:
        return True, "empty_text"

    module = _matcher_module_name(matcher_cls)
    if not module:
        return False, None

    command_matched = False
    matcher_commands = _extract_matcher_command_literals(matcher_cls)
    shortcut_match = _matcher_alconna_shortcut_matches(matcher_cls, text)
    if matcher_commands:
        for command in matcher_commands:
            if _matcher_command_matches(text, command):
                command_matched = True
                break
        else:
            if shortcut_match:
                command_matched = True
            else:
                return True, "command_miss"
    elif shortcut_match is False:
        return True, "command_miss"
    elif shortcut_match is True:
        command_matched = True

    ai_route_modules = dispatch_context.ai_route_modules or _collect_ai_route_modules(
        event, state
    )
    ai_route_heads = dispatch_context.ai_route_heads or _collect_ai_route_heads(
        event, state
    )
    if ai_route_modules and module not in ai_route_modules:
        if not _matcher_matches_ai_route_heads(matcher_cls, ai_route_heads):
            return True, "route_miss"
    elif ai_route_modules:
        return False, None

    if not _ROUTE_INDEX_READY:
        await _ensure_route_index()

    if module not in _ROUTE_MODULES_WITH_COMMANDS:
        return False, None

    route_modules = dispatch_context.route_modules or _get_route_modules_for_event(
        event, state
    )
    if module not in route_modules:
        if command_matched:
            return False, None
        return True, "route_miss"
    return False, None


def _check_matcher_prefilter_before_task(
    matcher_cls: type[Matcher],
    event: Event,
    state: dict | None = None,
    dispatch_context: EventDispatchContext | None = None,
) -> tuple[bool, str | None]:
    """Conservative selector before creating matcher task.

    This mirrors the async matcher prefilter but never performs IO or route-index
    rebuild. If anything is uncertain, let the existing check_and_run_matcher
    patch handle it inside the task.
    """
    if dispatch_context is None:
        dispatch_context = _context_from_state(state) or _build_dispatch_context_sync(
            event, state
        )

    event_type = event.get_type()
    matcher_type = getattr(matcher_cls, "type", "") or ""
    if isinstance(matcher_type, str) and matcher_type and matcher_type != event_type:
        return True, "type_miss"

    if event_type != "message":
        if _is_command_matcher_class(matcher_cls):
            return True, "non_message_command"
        return False, None

    if getattr(matcher_cls, "temp", False):
        return False, None

    if not _is_command_matcher_class(matcher_cls):
        lane = _dispatch_lane_for_matcher(matcher_cls, dispatch_context)
        if reason := _passive_signal_skip_reason(lane, dispatch_context):
            return True, reason
        return False, None

    text = dispatch_context.plain_text or _state_plain_text(state)
    if not text:
        text = _event_plain_text(event)
        if state is not None and text:
            state["_zx_plain_text"] = text
    if not text:
        return True, "empty_text"

    module = _matcher_module_name(matcher_cls)
    if not module:
        return False, None

    command_matched = False
    matcher_commands = _extract_matcher_command_literals(matcher_cls)
    shortcut_match = _matcher_alconna_shortcut_matches(matcher_cls, text)
    if matcher_commands:
        for command in matcher_commands:
            if _matcher_command_matches(text, command):
                command_matched = True
                break
        else:
            if shortcut_match:
                command_matched = True
            else:
                return True, "command_miss"
    elif shortcut_match is False:
        return True, "command_miss"
    elif shortcut_match is True:
        command_matched = True

    ai_route_modules = dispatch_context.ai_route_modules or _collect_ai_route_modules(
        event, state
    )
    ai_route_heads = dispatch_context.ai_route_heads or _collect_ai_route_heads(
        event, state
    )
    if ai_route_modules and module not in ai_route_modules:
        if not _matcher_matches_ai_route_heads(matcher_cls, ai_route_heads):
            return True, "route_miss"
    elif ai_route_modules:
        return False, None

    if not _ROUTE_INDEX_READY:
        return False, None

    if module not in _ROUTE_MODULES_WITH_COMMANDS:
        return False, None

    route_modules = dispatch_context.route_modules or _get_route_modules_for_event(
        event, state
    )
    if module not in route_modules:
        if command_matched:
            return False, None
        return True, "route_miss"
    return False, None


_MAX_MATCHER_CACHE = 512


async def _patched_check_and_run_matcher(
    Matcher: type[Matcher],
    bot: Bot,
    event: Event,
    state: dict,
    stack=None,
    dependency_cache=None,
) -> None:
    skip, reason = await _check_matcher_prefilter(
        Matcher, event, state if isinstance(state, dict) else None
    )
    _record_prefilter_stats(skip, reason, "inside_task")
    if skip:
        return

    original = _ORIGINAL_CHECK_AND_RUN_MATCHER
    if not original:
        return
    kwargs = {
        "Matcher": Matcher,
        "bot": bot,
        "event": event,
        "state": state,
        "stack": stack,
        "dependency_cache": dependency_cache,
    }
    await original(**kwargs)


def _install_matcher_prefilter() -> None:
    global _CHECK_MATCHER_PATCHED, _ORIGINAL_CHECK_AND_RUN_MATCHER
    if _CHECK_MATCHER_PATCHED:
        return
    _ORIGINAL_CHECK_AND_RUN_MATCHER = nb_message.check_and_run_matcher
    nb_message.check_and_run_matcher = _patched_check_and_run_matcher  # type: ignore[assignment]
    _CHECK_MATCHER_PATCHED = True


def _uninstall_matcher_prefilter() -> None:
    global _CHECK_MATCHER_PATCHED, _ORIGINAL_CHECK_AND_RUN_MATCHER
    if not _CHECK_MATCHER_PATCHED:
        return
    if _ORIGINAL_CHECK_AND_RUN_MATCHER is not None:
        nb_message.check_and_run_matcher = _ORIGINAL_CHECK_AND_RUN_MATCHER  # type: ignore[assignment]
    _CHECK_MATCHER_PATCHED = False
    _ORIGINAL_CHECK_AND_RUN_MATCHER = None


async def _patched_handle_event(bot: Bot, event: Event) -> None:
    show_log = True
    escape_tag = getattr(nb_message, "escape_tag")
    logger_ = getattr(nb_message, "logger")
    no_log_exception = getattr(nb_message, "NoLogException")

    log_msg = f"<m>{escape_tag(bot.type)} {escape_tag(bot.self_id)}</m> | "
    try:
        log_msg += event.get_log_string()
    except no_log_exception:
        show_log = False
    if show_log:
        logger_.opt(colors=True).success(log_msg)

    state = {}
    dependency_cache = {}
    async_exit_stack = getattr(nb_message, "AsyncExitStack")
    apply_event_preprocessors = getattr(nb_message, "_apply_event_preprocessors")
    apply_event_postprocessors = getattr(nb_message, "_apply_event_postprocessors")
    trie_rule = getattr(nb_message, "TrieRule")
    matchers = getattr(nb_message, "matchers")
    catch = getattr(nb_message, "catch")
    stop_propagation = getattr(nb_message, "StopPropagation")
    handle_exception = getattr(nb_message, "_handle_exception")
    anyio_mod = getattr(nb_message, "anyio")
    run_coro_with_shield = getattr(nb_message, "run_coro_with_shield")

    async with async_exit_stack() as stack:
        if not await apply_event_preprocessors(
            bot=bot,
            event=event,
            state=state,
            stack=stack,
            dependency_cache=dependency_cache,
        ):
            return

        try:
            trie_rule.get_value(bot, event, state)
        except Exception as e:
            logger_.opt(colors=True, exception=e).warning(
                "Error while parsing command for event"
            )
        _prepare_handle_event_state(event, state)
        dispatch_context = await _build_dispatch_context(event, state)
        dispatch_budget = _dispatch_budget_for_context(dispatch_context)

        break_flag = False

        def _handle_stop_propagation(_exc_group) -> None:
            nonlocal break_flag
            break_flag = True
            logger_.debug("Stop event propagation")

        for priority in sorted(matchers.keys()):
            if break_flag:
                break

            if show_log:
                logger_.debug(f"Checking for matchers in priority {priority}...")

            if not (priority_matchers := matchers[priority]):
                continue

            with catch(
                {
                    stop_propagation: _handle_stop_propagation,
                    Exception: handle_exception(
                        "<r><bg #f8bbd0>Error when checking Matcher.</bg #f8bbd0></r>"
                    ),
                }
            ):
                async with anyio_mod.create_task_group() as tg:
                    for matcher in priority_matchers:
                        skip, reason = _check_matcher_prefilter_before_task(
                            matcher,
                            event,
                            state,
                            dispatch_context,
                        )
                        _record_prefilter_stats(skip, reason, "before_task")
                        if skip:
                            lane = _dispatch_lane_for_matcher(matcher, dispatch_context)
                            _record_dispatch_selection(lane, False)
                            continue
                        lane = _dispatch_lane_for_matcher(matcher, dispatch_context)
                        if lane.startswith("passive_") and not _consume_dispatch_budget(
                            lane,
                            dispatch_budget,
                        ):
                            _record_dispatch_selection(lane, False)
                            continue
                        matcher_state = _build_matcher_state(state)
                        tg.start_soon(
                            run_coro_with_shield,
                            _run_selected_matcher(
                                matcher,
                                bot,
                                event,
                                matcher_state,
                                stack,
                                dependency_cache,
                                lane,
                            ),
                        )

        if show_log:
            logger_.debug("Checking for matchers completed")

        await apply_event_postprocessors(bot, event, state, stack, dependency_cache)


def _install_handle_event_selector() -> None:
    global _HANDLE_EVENT_PATCHED, _ORIGINAL_HANDLE_EVENT
    if _HANDLE_EVENT_PATCHED:
        return
    _ORIGINAL_HANDLE_EVENT = nb_message.handle_event
    nb_message.handle_event = _patched_handle_event  # type: ignore[assignment]
    for module_name in (
        "nonebot.adapters.onebot.v11.bot",
        "nonebot.adapters.onebot.v12.bot",
        "onebug.mixin.process",
    ):
        with contextlib.suppress(Exception):
            module = importlib.import_module(module_name)
            current = getattr(module, "handle_event", None)
            if current is not None:
                _ORIGINAL_ADAPTER_HANDLE_EVENTS[module] = current
                setattr(module, "handle_event", _patched_handle_event)
    _HANDLE_EVENT_PATCHED = True


def _uninstall_handle_event_selector() -> None:
    global _HANDLE_EVENT_PATCHED, _ORIGINAL_HANDLE_EVENT
    if not _HANDLE_EVENT_PATCHED:
        return
    if _ORIGINAL_HANDLE_EVENT is not None:
        nb_message.handle_event = _ORIGINAL_HANDLE_EVENT  # type: ignore[assignment]
    for module, original in list(_ORIGINAL_ADAPTER_HANDLE_EVENTS.items()):
        with contextlib.suppress(Exception):
            setattr(module, "handle_event", original)
    _ORIGINAL_ADAPTER_HANDLE_EVENTS.clear()
    _HANDLE_EVENT_PATCHED = False
    _ORIGINAL_HANDLE_EVENT = None


async def _get_route_context(text: str, event_cache: dict | None) -> set[str]:
    if not text:
        return set()
    if event_cache is not None and "route_modules" in event_cache:
        return event_cache["route_modules"]
    await _ensure_route_index()
    matched = _match_route_modules(text)
    if event_cache is not None:
        event_cache["route_modules"] = matched
    return matched


async def _cache_sweep_loop() -> None:
    while True:
        await asyncio.sleep(CACHE_SWEEP_INTERVAL)
        with contextlib.suppress(Exception):
            if EVENT_CACHE is not None:
                _ = len(EVENT_CACHE)
            _ = len(_CHECK_MATCHER_ROUTE_CACHE)
            for _mc in (
                _MATCHER_COMMAND_TYPE_CACHE,
                _MATCHER_COMMAND_LITERAL_CACHE,
                _MATCHER_ALCONNA_SHORTCUT_CACHE,
            ):
                if len(_mc) > _MAX_MATCHER_CACHE:
                    _mc.clear()


async def start_auth_runtime_tasks() -> None:
    global _CACHE_SWEEP_TASK
    await _ensure_route_index()
    _install_matcher_prefilter()
    _install_handle_event_selector()
    if _CACHE_SWEEP_TASK is None or _CACHE_SWEEP_TASK.done():
        _CACHE_SWEEP_TASK = asyncio.create_task(_cache_sweep_loop())


async def stop_auth_runtime_tasks() -> None:
    global _CACHE_SWEEP_TASK
    _uninstall_handle_event_selector()
    _uninstall_matcher_prefilter()
    task = _CACHE_SWEEP_TASK
    _CACHE_SWEEP_TASK = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task


async def _has_limits_cached(module: str, event_cache: dict | None) -> bool:
    module_limit_cache: dict[str, bool] = {}
    if event_cache is not None:
        module_limit_cache = event_cache.setdefault("module_limits", {})
    if module in module_limit_cache:
        return module_limit_cache[module]
    limits = await LimitManager.get_module_limits(module)
    has_limits = bool(limits)
    module_limit_cache[module] = has_limits
    return has_limits


@contextlib.asynccontextmanager
async def _db_section():
    global DB_ACTIVE_COUNT
    if DB_SEMAPHORE.locked():
        logger.warning(
            "db semaphore saturated, allowing permission check to continue",
            LOGGER_COMMAND,
        )
        raise PermissionExemption("db semaphore saturated, allow pass")
    await DB_SEMAPHORE.acquire()
    DB_ACTIVE_COUNT += 1
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            DB_SEMAPHORE.release()
        DB_ACTIVE_COUNT = max(DB_ACTIVE_COUNT - 1, 0)


async def _get_group_cached(entity, event_cache) -> GroupSnapshot | None:
    if not entity.group_id:
        return None
    if event_cache is not None and "group" in event_cache:
        return event_cache["group"]
    group = GroupMemoryCache.get_if_ready(entity.group_id, entity.channel_id)
    if event_cache is not None:
        event_cache["group"] = group
    return group


def _module_in_block_string(module: str, value: str | None) -> bool:
    if not value:
        return False
    return f"<{module}," in value


def _group_has_plugin_block(group, module: str) -> bool:
    if not group:
        return False
    block_set = getattr(group, "block_plugin_set", None)
    super_block_set = getattr(group, "superuser_block_plugin_set", None)
    if block_set is not None or super_block_set is not None:
        if block_set and module in block_set:
            return True
        if super_block_set and module in super_block_set:
            return True
        return False
    block_plugin = getattr(group, "block_plugin", "") or ""
    super_block_plugin = getattr(group, "superuser_block_plugin", "") or ""
    return _module_in_block_string(module, block_plugin) or _module_in_block_string(
        module, super_block_plugin
    )


def _needs_auth_plugin(plugin: PluginInfo, context: PermissionContext) -> bool:
    group = context.group
    entity = context.entity
    if plugin.block_type == BlockType.ALL and not plugin.status:
        if group and getattr(group, "is_super", False):
            return False
        return True
    if entity.group_id:
        if plugin.block_type == BlockType.GROUP:
            return True
        return _group_has_plugin_block(group, plugin.module)
    return plugin.block_type == BlockType.PRIVATE


def _needs_admin_check(plugin: PluginInfo) -> bool:
    if plugin.admin_level and plugin.admin_level > 0:
        return True
    return plugin.plugin_type in {
        PluginType.ADMIN,
        PluginType.SUPERUSER,
        PluginType.SUPER_AND_ADMIN,
    }


async def _get_bot_data_cached(
    bot_id: str, event_cache
) -> tuple[BotSnapshot | None, bool]:
    if event_cache is not None and "bot_data" in event_cache:
        return event_cache.get("bot_data"), event_cache.get("bot_timeout", False)
    bot = await BotMemoryCache.get(bot_id)
    if event_cache is not None:
        event_cache["bot_data"] = bot
        event_cache["bot_timeout"] = False
    return bot, False


async def _get_admin_levels_cached(
    entity, event_cache
) -> tuple[tuple[LevelUserSnapshot | None, LevelUserSnapshot | None] | None, bool]:
    if event_cache is not None and "admin_levels" in event_cache:
        return event_cache.get("admin_levels"), event_cache.get("admin_timeout", False)
    levels = await LevelUserMemoryCache.get_levels(entity.user_id, entity.group_id)
    if event_cache is not None:
        event_cache["admin_levels"] = levels
        event_cache["admin_timeout"] = False
    return levels, False


# 超时装饰器
async def with_timeout(coro, timeout=TIMEOUT_SECONDS, name=None):
    """带超时控制的协程执行

    参数:
        coro: 要执行的协程
        timeout: 超时时间（秒）
        name: 操作名称，用于日志记录

    返回:
        协程的返回值，或者在超时时抛出 TimeoutError
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        if name:
            logger.error(f"{name} 操作超时 (>{timeout}s)", LOGGER_COMMAND)
            # 更新熔断计数器
            if name in CIRCUIT_BREAKERS:
                CIRCUIT_BREAKERS[name]["failures"] += 1
                if (
                    CIRCUIT_BREAKERS[name]["failures"]
                    >= CIRCUIT_BREAKERS[name]["threshold"]
                    and not CIRCUIT_BREAKERS[name]["active"]
                ):
                    CIRCUIT_BREAKERS[name]["active"] = True
                    CIRCUIT_BREAKERS[name]["reset_time"] = (
                        time.time() + CIRCUIT_RESET_TIME
                    )
                    logger.warning(
                        f"{name} 熔断器已激活，将在 {CIRCUIT_RESET_TIME} 秒后重置",
                        LOGGER_COMMAND,
                    )
        raise


# 检查熔断状态
def check_circuit_breaker(name):
    """检查熔断器状态

    参数:
        name: 操作名称

    返回:
        bool: 是否已熔断
    """
    if name not in CIRCUIT_BREAKERS:
        return False

    # 检查是否需要重置熔断器
    if (
        CIRCUIT_BREAKERS[name]["active"]
        and time.time() > CIRCUIT_BREAKERS[name]["reset_time"]
    ):
        CIRCUIT_BREAKERS[name]["active"] = False
        CIRCUIT_BREAKERS[name]["failures"] = 0
        logger.info(f"{name} 熔断器已重置", LOGGER_COMMAND)

    return CIRCUIT_BREAKERS[name]["active"]


def _is_hidden_plugin(matcher: Matcher) -> bool:
    plugin = matcher.plugin
    if not plugin or not plugin.metadata:
        return False
    extra = plugin.metadata.extra or {}
    return extra.get("plugin_type") == PluginType.HIDDEN


async def _fetch_user_readonly(
    user_dao: DataAccess, user_id: str
) -> UserConsole | None:
    return await with_timeout(
        user_dao.safe_get_or_none(user_id=user_id), name="get_user"
    )


async def get_plugin_and_user(
    module: str,
    user_id: str,
    platform: str | None = None,
    event_cache: dict | None = None,
    need_user: bool = True,
) -> tuple[PluginInfo, UserConsole | None]:
    """Fetch plugin info and read user only when cost is required."""
    user_dao = DataAccess(UserConsole)

    plugin = None
    if event_cache is not None:
        plugin_cache = event_cache.setdefault("plugin_cache", {})
        if module in plugin_cache:
            plugin = plugin_cache[module]
    if plugin is None:
        plugin = await PluginInfoMemoryCache.get_by_module(module)
        if event_cache is not None:
            event_cache.setdefault("plugin_cache", {})[module] = plugin
    plugin = cast(PluginInfo | None, plugin)

    if not plugin:
        raise PermissionExemption(f"plugin:{module} not found, skip permission check")
    if plugin.plugin_type == PluginType.HIDDEN:
        raise PermissionExemption(f"plugin {plugin.name}:{plugin.module} hidden, skip")

    user = None
    if need_user and plugin.cost_gold > 0:
        if event_cache is not None:
            user_cache = event_cache.setdefault("user_cache", {})
            if user_id in user_cache:
                user = user_cache[user_id]
            else:
                try:
                    async with _db_section():
                        user = await _fetch_user_readonly(user_dao, user_id)
                except PermissionExemption:
                    user = None
                user_cache[user_id] = user
        else:
            try:
                async with _db_section():
                    user = await _fetch_user_readonly(user_dao, user_id)
            except PermissionExemption:
                user = None

    return plugin, user


async def get_plugin_cost(
    user: UserConsole | None,
    plugin: PluginInfo,
    session: Uninfo,
    *,
    context: PermissionContext | None = None,
) -> int:
    """获取插件费用

    参数:
        bot: Bot
        user: 用户数据
        plugin: 插件数据
        session: Uninfo

    异常:
        IsSuperuserException: 超级用户
        IsSuperuserException: 超级用户

    返回:
        int: 调用插件金币费用
    """
    cost_gold = await with_timeout(
        auth_cost(user, plugin, session, context=context), name="auth_cost"
    )
    is_superuser = context.is_superuser if context is not None else False
    if is_superuser:
        if plugin.plugin_type == PluginType.SUPERUSER:
            raise IsSuperuserException()
        if not plugin.limit_superuser:
            raise IsSuperuserException()
    return cost_gold


async def reduce_gold(user_id: str, module: str, cost_gold: int, session: Uninfo):
    """扣除用户金币

    参数:
        user_id: 用户id
        module: 插件模块名称
        cost_gold: 消耗金币
        session: Uninfo
    """
    should_clear_cache = False
    try:
        await with_timeout(
            UserConsole.reduce_gold(
                user_id,
                cost_gold,
                GoldHandle.PLUGIN,
                module,
                PlatformUtils.get_platform(session),
            ),
            name="reduce_gold",
        )
    except InsufficientGold:
        if u := await UserConsole.get_user(user_id):
            u.gold = 0
            await u.save(update_fields=["gold"])
    except asyncio.TimeoutError:
        should_clear_cache = True
        logger.error(
            f"扣除金币超时，用户: {user_id}, 金币: {cost_gold}",
            LOGGER_COMMAND,
            session=session,
        )

    # 正常写入路径由 UserConsole.save() 统一失效缓存；超时状态不确定时兜底清理。
    if should_clear_cache:
        await DataAccess(UserConsole).clear_cache(user_id=user_id)
    logger.debug(f"调用功能花费金币: {cost_gold}", LOGGER_COMMAND, session=session)


# 辅助函数，用于记录每个 hook 的执行时间
async def time_hook(coro, name, recorder: HookTraceRecorder | None = None):
    start = time.time()
    try:
        # 检查熔断状态
        if check_circuit_breaker(name):
            logger.info(f"{name} 熔断器激活中，跳过执行", LOGGER_COMMAND)
            if recorder is not None:
                recorder.set(name, "熔断跳过")
            return

        # 添加超时控制
        return await with_timeout(coro, name=name)
    except asyncio.TimeoutError:
        if recorder is not None:
            recorder.set(name, f"超时 (>{TIMEOUT_SECONDS}s)")
    finally:
        if recorder is not None and not recorder.contains(name):
            recorder.set(name, f"{time.time() - start:.3f}s")


async def _enter_hooks_section():
    """尝试获取全局信号量并更新计数器，饱和时快速放行。"""
    global HOOKS_ACTIVE_COUNT
    if HOOKS_SEMAPHORE.locked():
        logger.warning(
            "hooks semaphore saturated, allowing pass",
            LOGGER_COMMAND,
        )
        raise PermissionExemption("hooks semaphore saturated, allow pass")
    await HOOKS_SEMAPHORE.acquire()
    HOOKS_ACTIVE_COUNT += 1


async def _leave_hooks_section():
    """释放信号量并更新计数器。"""
    global HOOKS_ACTIVE_COUNT
    with contextlib.suppress(Exception):
        HOOKS_SEMAPHORE.release()
    HOOKS_ACTIVE_COUNT = max(HOOKS_ACTIVE_COUNT - 1, 0)


async def route_precheck(
    matcher: Matcher,
    context: EventContext,
) -> bool:
    module = matcher.plugin_name or ""
    if not module:
        return False
    if _is_hidden_plugin(matcher):
        return False
    if not _is_command_matcher_class(type(matcher)):
        return False

    route_modules = context.route_modules if context.route_modules_loaded else None
    if route_modules is None:
        route_modules = await _get_route_context(
            context.plain_text,
            context.event_cache,
        )
        set_route_modules(None, context, route_modules)

    if module in _ROUTE_MODULES_WITH_COMMANDS and module not in route_modules:
        if _matcher_has_alconna_shortcuts(type(matcher)):
            return False
        if context.event_cache is not None:
            context.event_cache["route_skip"] = True
        return True
    return False


async def auth(
    matcher: Matcher,
    event: Event,
    bot: Bot,
    session: Uninfo,
    *,
    context: EventContext,
    skip_ban: bool = False,
    state: dict | None = None,
):
    """权限检查

    参数:
        matcher: matcher
        event: Event
        bot: bot
        session: Uninfo
        context: EventContext
    """
    start_time = time.time()
    cost_gold = 0
    ignore_flag = False
    entity = context.entity
    event_cache = context.event_cache
    text = context.plain_text
    is_superuser = context.is_superuser
    route_modules = context.route_modules if context.route_modules_loaded else None
    module = matcher.plugin_name or ""
    is_command_matcher = _is_command_matcher_class(type(matcher))
    auth_allowed = None
    auth_result_cache = None
    admin_checked_pre = False
    permission_context: PermissionContext | None = None
    side_effect_cache = get_permission_side_effect_cache(
        state=state,
        event_cache=event_cache,
    )
    side_effect_lock = None
    entered_side_effect_lock = False

    # 仅在慢请求时记录 hook 明细，避免热路径高频构造字符串
    hook_recorder = HookTraceRecorder(start_time)
    hooks_time = 0  # 初始化 hooks_time 变量

    # 记录是否已进入 hooks 区域（用于 finally 中释放）
    entered_hooks = False

    try:
        if not module:
            auth_allowed = True
            return

        side_effect_lock = side_effect_cache.lock_for(module)
        await side_effect_lock.acquire()
        entered_side_effect_lock = True

        auth_result_cache = side_effect_cache.auth_results
        cached_result = auth_result_cache.get(module)
        if cached_result is not None:
            allowed, reason = cached_result
            if not allowed:
                raise SkipPluginException(reason or "auth cached skip")
            return

        if _is_hidden_plugin(matcher):
            auth_allowed = True
            return
        if event_cache is not None and event_cache.get("ban_state") is True:
            raise SkipPluginException("user or group banned (cached)")

        if route_modules is None:
            route_modules = await _get_route_context(text, event_cache)
            set_route_modules(state, context, route_modules)
        route_skip_checks = (
            is_command_matcher
            and module in _ROUTE_MODULES_WITH_COMMANDS
            and module not in route_modules
            and not _matcher_has_alconna_shortcuts(type(matcher))
        )
        if route_skip_checks:
            if event_cache is not None:
                event_cache["route_skip"] = True
            hook_recorder.set("route", "miss")
            auth_allowed = True
            return

        platform = context.platform
        # 获取插件和用户数据
        plugin_user_start = time.time()
        try:
            plugin, user = await with_timeout(
                get_plugin_and_user(
                    module,
                    entity.user_id,
                    platform,
                    event_cache=event_cache,
                    need_user=not route_skip_checks,
                ),
                name="get_plugin_and_user",
            )
            hook_recorder.set(
                "get_plugin_user", f"{time.time() - plugin_user_start:.3f}s"
            )
        except asyncio.TimeoutError:
            logger.error(
                f"获取插件和用户数据超时，模块: {module}",
                LOGGER_COMMAND,
                session=session,
            )
            auth_allowed = True
            return

        permission_context = PermissionContext(
            event=context,
            module=module,
            plugin=plugin,
            user=user,
        )
        store_permission_context(state, permission_context)

        if not route_skip_checks and _needs_admin_check(plugin):
            if plugin.plugin_type in {
                PluginType.SUPERUSER,
                PluginType.SUPER_AND_ADMIN,
            }:
                if is_superuser:
                    hook_recorder.set("auth_admin", "superuser")
                    admin_checked_pre = True
                elif plugin.plugin_type == PluginType.SUPERUSER:
                    raise SkipPluginException("超级管理员权限不足...")
            if not admin_checked_pre:
                if event_cache is not None and event_cache.get("admin_precheck_done"):
                    hook_recorder.set("auth_admin", "precheck")
                    admin_checked_pre = True
                else:
                    await LevelUserMemoryCache.ensure_fresh()
                    admin_levels = None
                    admin_timeout = False
                    if event_cache is not None:
                        admin_levels, admin_timeout = await _get_admin_levels_cached(
                            entity, event_cache
                        )
                    permission_context.admin_levels = admin_levels
                    if admin_timeout:
                        hook_recorder.set("auth_admin", "timeout")
                    else:
                        admin_start = time.time()
                        await auth_admin(
                            plugin,
                            session,
                            context=permission_context,
                        )
                        hook_recorder.set(
                            "auth_admin", f"{time.time() - admin_start:.3f}s(pre)"
                        )
                admin_checked_pre = True

        ban_cache_state = None
        if event_cache is not None:
            ban_cache_state = event_cache.get("ban_state")
        if ban_cache_state is True:
            hook_recorder.set("auth_ban", "cached")
            raise SkipPluginException("user or group banned (cached)")
        if ban_cache_state is False:
            hook_recorder.set("auth_ban", "cached")
        elif ban_cache_state is None:
            if skip_ban:
                hook_recorder.set("auth_ban", "skipped")
            else:
                ban_start = time.time()
                try:
                    await auth_ban(
                        matcher,
                        session,
                        plugin,
                        context=permission_context,
                    )
                    hook_recorder.set("auth_ban", f"{time.time() - ban_start:.3f}s")
                    if event_cache is not None:
                        event_cache["ban_state"] = False
                except SkipPluginException:
                    hook_recorder.set("auth_ban", f"{time.time() - ban_start:.3f}s")
                    if event_cache is not None:
                        event_cache["ban_state"] = True
                    raise

        # 获取插件费用
        if not route_skip_checks and plugin.cost_gold > 0:
            cost_start = time.time()
            try:
                cost_gold = await with_timeout(
                    get_plugin_cost(
                        user,
                        plugin,
                        session,
                        context=permission_context,
                    ),
                    name="get_plugin_cost",
                )
                hook_recorder.set("cost_gold", f"{time.time() - cost_start:.3f}s")
            except asyncio.TimeoutError:
                logger.error(
                    f"获取插件费用超时，模块: {module}", LOGGER_COMMAND, session=session
                )
                # 继续执行，不阻止权限检查
        else:
            hook_recorder.set("cost_gold", "skipped")

        # 执行 bot_filter
        bot_filter(session, context=permission_context)

        group = await _get_group_cached(entity, event_cache)

        bot_data = None
        bot_timeout = False
        if event_cache is not None:
            bot_data, bot_timeout = await _get_bot_data_cached(bot.self_id, event_cache)

        admin_levels = None
        admin_timeout = False
        if (
            not admin_checked_pre
            and plugin.admin_level
            and event_cache is not None
            and not route_skip_checks
        ):
            admin_levels, admin_timeout = await _get_admin_levels_cached(
                entity, event_cache
            )

        permission_context.group = group
        permission_context.bot_data = bot_data
        if admin_levels is not None:
            permission_context.admin_levels = admin_levels
        store_permission_context(state, permission_context)

        # 并行执行所有 hook 检查，并记录执行时间
        hooks_start = time.time()
        allow_sleep_bypass = _is_bot_wake_command(module, text)

        # 先进入 hooks 并行检查区域；饱和时快速放行，避免创建并积压协程。
        await _enter_hooks_section()
        entered_hooks = True

        # 创建所有 hook 任务
        hook_tasks = []
        if event_cache is None:
            hook_tasks.append(
                time_hook(
                    auth_bot(
                        plugin,
                        bot.self_id,
                        allow_sleep_bypass=allow_sleep_bypass,
                        context=permission_context,
                    ),
                    "auth_bot",
                    hook_recorder,
                )
            )
        else:
            if bot_timeout:
                hook_recorder.set("auth_bot", "timeout")
            else:
                hook_tasks.append(
                    time_hook(
                        auth_bot(
                            plugin,
                            bot.self_id,
                            bot_data=bot_data,
                            skip_fetch=True,
                            allow_sleep_bypass=allow_sleep_bypass,
                            context=permission_context,
                        ),
                        "auth_bot",
                        hook_recorder,
                    )
                )

        if is_superuser:
            hook_recorder.set("auth_group", "superuser")
        else:
            hook_tasks.append(
                time_hook(
                    auth_group(
                        plugin,
                        group,
                        text,
                        entity.group_id,
                        context=permission_context,
                    ),
                    "auth_group",
                    hook_recorder,
                )
            )

        if not route_skip_checks and plugin.admin_level and not admin_checked_pre:
            if event_cache is None:
                hook_tasks.append(
                    time_hook(
                        auth_admin(plugin, session, context=permission_context),
                        "auth_admin",
                        hook_recorder,
                    )
                )
            else:
                if admin_timeout:
                    hook_recorder.set("auth_admin", "timeout")
                else:
                    hook_tasks.append(
                        time_hook(
                            auth_admin(
                                plugin,
                                session,
                                context=permission_context,
                            ),
                            "auth_admin",
                            hook_recorder,
                        )
                    )
        else:
            hook_recorder.setdefault("auth_admin", "skipped")

        if is_superuser:
            hook_recorder.set("auth_plugin", "superuser")
        elif not route_skip_checks and _needs_auth_plugin(plugin, permission_context):
            hook_tasks.append(
                time_hook(
                    auth_plugin(
                        plugin,
                        group,
                        session,
                        event,
                        context=permission_context,
                        skip_group_block=is_superuser,
                    ),
                    "auth_plugin",
                    hook_recorder,
                )
            )
        else:
            hook_recorder.set("auth_plugin", "skipped")

        if not route_skip_checks:
            has_limits = await _has_limits_cached(module, event_cache)
            if has_limits:
                hook_tasks.append(
                    time_hook(
                        auth_limit(plugin, session, context=permission_context),
                        "auth_limit",
                        hook_recorder,
                    )
                )
            else:
                hook_recorder.set("auth_limit", "skipped")
        else:
            hook_recorder.set("auth_limit", "skipped")

        # 使用 gather 并行执行所有 hook，但添加总体超时控制
        try:
            await with_timeout(
                asyncio.gather(*hook_tasks),
                timeout=TIMEOUT_SECONDS * 2,  # 给总体执行更多时间
                name="auth_hooks_gather",
            )
        except asyncio.TimeoutError:
            logger.error(
                f"权限检查 hooks 总体执行超时，模块: {module}",
                LOGGER_COMMAND,
                session=session,
            )
            # 不抛出异常，允许继续执行

        hooks_time = time.time() - hooks_start
        auth_allowed = True

    except SkipPluginException as e:
        LimitManager.unblock(module, entity.user_id, entity.group_id, entity.channel_id)
        if e.tip_message:
            try:
                tip_coro = send_message(
                    session,
                    e.tip_message,
                    e.tip_check_tag,
                    background=e.tip_background,
                )
                if e.tip_timeout and not e.tip_background:
                    await asyncio.wait_for(tip_coro, timeout=e.tip_timeout)
                else:
                    await tip_coro
            except asyncio.TimeoutError:
                logger.error("发送权限提示超时", LOGGER_COMMAND, session=session)
        logger.info(str(e), LOGGER_COMMAND, session=session)
        ignore_flag = True
        auth_allowed = False
    except IsSuperuserException:
        logger.debug("超级用户跳过权限检测...", LOGGER_COMMAND, session=session)
        auth_allowed = True
    except PermissionExemption as e:
        logger.info(str(e), LOGGER_COMMAND, session=session)
        auth_allowed = True
    finally:
        # 如果进入过 hooks 区域，确保释放信号量（即使上层处理抛出了异常）
        if entered_hooks:
            try:
                await _leave_hooks_section()
            except Exception:
                logger.error(
                    "释放 hooks 信号量时出错",
                    LOGGER_COMMAND,
                    session=session,
                )
        if auth_result_cache is not None and auth_allowed is not None:
            auth_result_cache[module] = (auth_allowed, None)
        if entered_side_effect_lock and side_effect_lock is not None:
            with contextlib.suppress(Exception):
                side_effect_lock.release()
    # 扣除金币
    if not ignore_flag and cost_gold > 0:
        gold_start = time.time()
        try:
            await with_timeout(
                reduce_gold(entity.user_id, module, cost_gold, session),
                name="reduce_gold",
            )
            hook_recorder.set("reduce_gold", f"{time.time() - gold_start:.3f}s")
        except asyncio.TimeoutError:
            logger.error(
                f"扣除金币超时，模块: {module}", LOGGER_COMMAND, session=session
            )

    # 记录总执行时间
    total_time = time.time() - start_time
    if total_time > WARNING_THRESHOLD:  # 如果总时间超过500ms，记录详细信息
        logger.warning(
            f"权限检查耗时过长: {total_time:.3f}s, 模块: {module}, "
            f"hooks时间: {hooks_time:.3f}s, "
            f"详情: {hook_recorder.snapshot()}",
            LOGGER_COMMAND,
            session=session,
        )

    if ignore_flag:
        raise IgnoredException("权限检测 ignore")

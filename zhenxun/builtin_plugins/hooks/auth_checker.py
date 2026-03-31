import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import os
import re
import time
from typing import cast

from nonebot import get_loaded_plugins
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
import nonebot.message as nb_message
from nonebot_plugin_alconna import UniMsg
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
from zhenxun.utils.utils import get_entity_ids

from .auth.auth_admin import auth_admin
from .auth.auth_ban import auth_ban, is_ban
from .auth.auth_bot import auth_bot
from .auth.auth_cost import auth_cost
from .auth.auth_group import auth_group
from .auth.auth_limit import LimitManager, auth_limit
from .auth.auth_plugin import auth_plugin
from .auth.bot_filter import bot_filter
from .auth.config import LOGGER_COMMAND, WARNING_THRESHOLD
from .auth.exception import (
    IsSuperuserException,
    PermissionExemption,
    SkipPluginException,
)

AUTH_HOOKS_CONCURRENCY_LIMIT = 5
AUTH_DB_CONCURRENCY_LIMIT = 6
AUTH_PLUGIN_CACHE_TTL = 30
AUTH_USER_CACHE_TTL = 5
AUTH_EVENT_CACHE_TTL = 2


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

PLUGIN_CACHE_TTL = AUTH_PLUGIN_CACHE_TTL
USER_CACHE_TTL = AUTH_USER_CACHE_TTL

PLUGIN_CACHE = (
    CacheDict("AUTH_PLUGIN_CACHE", expire=PLUGIN_CACHE_TTL)
    if PLUGIN_CACHE_TTL > 0
    else None
)
USER_CACHE = (
    CacheDict("AUTH_USER_CACHE", expire=USER_CACHE_TTL) if USER_CACHE_TTL > 0 else None
)
EVENT_CACHE_TTL = AUTH_EVENT_CACHE_TTL
EVENT_CACHE = (
    CacheDict("AUTH_EVENT_CACHE", expire=EVENT_CACHE_TTL)
    if EVENT_CACHE_TTL > 0
    else None
)

# 路由索引缓存
_ROUTE_INDEX_LOCK = asyncio.Lock()
_ROUTE_INDEX_READY = False
_ROUTE_COMMAND_MAP: dict[str, set[str]] = {}
_ROUTE_PREFIX_MAP: dict[str, set[str]] = {}
_ROUTE_MODULES_WITH_COMMANDS: set[str] = set()
MATCHER_ROUTE_PREFILTER_TTL = 2
PREFILTER_STATS_LOG_INTERVAL = 10.0
CACHE_SWEEP_INTERVAL = 1.0

CPU_COUNT = os.cpu_count() or 4
COMMAND_MATCHER_CONCURRENCY = max(8, min(48, CPU_COUNT * 4))
HEAVY_COMMAND_CONCURRENCY = max(1, min(3, CPU_COUNT // 2))
HEAVY_COMMAND_MODULES = frozenset({"shop", "sign_in"})

# 全局信号量与计数器
HOOKS_ACTIVE_COUNT = 0
HOOKS_ACTIVE_LOCK = asyncio.Lock()
HOOKS_SEMAPHORE = asyncio.Semaphore(HOOKS_CONCURRENCY_LIMIT)
COMMAND_MATCHER_SEMAPHORE = asyncio.Semaphore(COMMAND_MATCHER_CONCURRENCY)
HEAVY_COMMAND_SEMAPHORE = asyncio.Semaphore(HEAVY_COMMAND_CONCURRENCY)

DB_SEMAPHORE = asyncio.Semaphore(DB_CONCURRENCY_LIMIT)
DB_ACTIVE_COUNT = 0
DB_ACTIVE_LOCK = asyncio.Lock()
_CHECK_MATCHER_PATCHED = False
_ORIGINAL_CHECK_AND_RUN_MATCHER: Callable[..., Awaitable[None]] | None = None
_MATCHER_COMMAND_TYPE_CACHE: dict[type[Matcher], bool] = {}
_MATCHER_COMMAND_LITERAL_CACHE: dict[type[Matcher], tuple[str, ...] | None] = {}
_MATCHER_ALCONNA_SHORTCUT_CACHE: dict[type[Matcher], bool] = {}
_CHECK_MATCHER_ROUTE_CACHE = CacheDict(
    "AUTH_MATCHER_ROUTE_CACHE", expire=MATCHER_ROUTE_PREFILTER_TTL
)
_PREFILTER_STATS = {
    "checked": 0,
    "skipped": 0,
    "type_miss": 0,
    "route_miss": 0,
    "command_miss": 0,
    "empty_text": 0,
}
_PREFILTER_LAST_LOG = 0.0
_CACHE_SWEEP_TASK: asyncio.Task | None = None
_BOT_WAKE_COMMAND_PATTERN = re.compile(r"^bot醒来(?:\s+\S+)?$", re.IGNORECASE)
_BOT_WAKE_CANONICAL_PATTERN = re.compile(
    r"^bot_manage\s+bot_switch\s+enable(?:\s+\S+)?$", re.IGNORECASE
)


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


def _cache_get(cache: CacheDict | None, key: str):
    if not cache:
        return None
    try:
        return cache[key]
    except KeyError:
        return None


def _cache_set(cache: CacheDict | None, key: str, value):
    if cache:
        cache[key] = value


def _debug_log(message: str, *args, **kwargs) -> None:
    if is_overloaded():
        return
    logger.debug(message, *args, **kwargs)


def _event_cache_key(event: Event, session: Uninfo, entity) -> str:
    msg_id = getattr(event, "message_id", None)
    if msg_id is None:
        msg_id = getattr(event, "id", None)
    if msg_id is None:
        msg_id = id(event)
    platform = PlatformUtils.get_platform(session)
    group_id = entity.group_id or ""
    channel_id = entity.channel_id or ""
    return (
        f"{platform}:{session.self_id}:{entity.user_id}:"
        f"{group_id}:{channel_id}:{msg_id}"
    )


def _get_event_cache(event: Event, session: Uninfo, entity):
    if not EVENT_CACHE:
        return None
    key = _event_cache_key(event, session, entity)
    try:
        return EVENT_CACHE[key]
    except KeyError:
        cache = {}
        EVENT_CACHE[key] = cache
        return cache


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
    if not matcher_commands:
        return False
    for command in matcher_commands:
        normalized_command = command.strip().casefold()
        if not normalized_command:
            continue
        for head in ai_route_heads:
            if not head:
                continue
            if _command_matches(head, normalized_command) or _command_matches(
                normalized_command, head
            ):
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
    with contextlib.suppress(Exception):
        return (event.get_plaintext() or "").strip()
    return ""


def _state_plain_text(state: dict | None) -> str:
    if state is None:
        return ""
    text = state.get("_zx_plain_text")
    if isinstance(text, str):
        return text.strip()
    return ""


def _get_route_modules_for_event(event: Event, state: dict | None = None) -> set[str]:
    if state is not None:
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
        state["_zx_route_modules"] = route_modules
    return route_modules


def _record_prefilter_stats(skipped: bool, reason: str | None) -> None:
    global _PREFILTER_LAST_LOG
    _PREFILTER_STATS["checked"] += 1
    if skipped:
        _PREFILTER_STATS["skipped"] += 1
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
    if isinstance(value, list | tuple | set | frozenset):
        for item in value:
            _collect_command_literals(item, target, depth + 1)
        return
    for attr in ("command", "commands", "cmd", "cmds"):
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


def _matcher_has_alconna_shortcuts(matcher_cls: type[Matcher]) -> bool:
    cached = _MATCHER_ALCONNA_SHORTCUT_CACHE.get(matcher_cls)
    if cached is not None:
        return cached

    has_shortcuts = False
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

        # Alconna matcher supports shortcut-based parsing (regex/fuzzy expansion).
        # Route prefilter only knows literal command heads, so shortcut matchers
        # must bypass strict route miss to avoid false negative skips.
        command_ref = getattr(call, "command", None)
        command = None
        if callable(command_ref):
            with contextlib.suppress(Exception):
                command = command_ref()
                if command is not None:
                    get_shortcuts = getattr(command, "get_shortcuts", None)
                    if callable(get_shortcuts):
                        shortcuts = get_shortcuts()
                        if shortcuts:
                            has_shortcuts = True
                            break
        formatter = getattr(command, "formatter", None)
        if formatter is not None:
            with contextlib.suppress(Exception):
                data = getattr(formatter, "data", None)
                if isinstance(data, dict):
                    for trace in data.values():
                        if getattr(trace, "shortcuts", None):
                            has_shortcuts = True
                            break
        if has_shortcuts:
            break

    _MATCHER_ALCONNA_SHORTCUT_CACHE[matcher_cls] = has_shortcuts
    return has_shortcuts


def _is_heavy_command_module(module: str) -> bool:
    normalized = module.strip().lower()
    if not normalized:
        return False
    if normalized in HEAVY_COMMAND_MODULES:
        return True
    return any(normalized.endswith(f".{name}") for name in HEAVY_COMMAND_MODULES)


async def _check_matcher_prefilter(
    matcher_cls: type[Matcher], event: Event, state: dict | None = None
) -> tuple[bool, str | None]:
    event_type = event.get_type()
    matcher_type = getattr(matcher_cls, "type", "") or ""
    if isinstance(matcher_type, str) and matcher_type and matcher_type != event_type:
        # Explicit matcher type mismatch cannot match this event.
        return True, "type_miss"

    if event_type != "message":
        return False, None

    # Session continuation matchers generated by pause/reject are temp=True.
    # They must bypass command-route prefilter, otherwise follow-up messages
    # (e.g. got_path waiting for plain text) will be dropped.
    if getattr(matcher_cls, "temp", False):
        return False, None

    is_command_matcher = _is_command_matcher_class(matcher_cls)
    if not is_command_matcher:
        return False, None

    text = _state_plain_text(state)
    if is_command_matcher and not text:
        text = _event_plain_text(event)
        if state is not None and text:
            state["_zx_plain_text"] = text
    if is_command_matcher and not text:
        return True, "empty_text"

    module = _matcher_module_name(matcher_cls)
    if not module:
        return False, None

    ai_route_modules = _collect_ai_route_modules(event, state)
    ai_route_heads = _collect_ai_route_heads(event, state)
    if ai_route_modules and module not in ai_route_modules:
        if not _matcher_matches_ai_route_heads(matcher_cls, ai_route_heads):
            return True, "route_miss"

    if not _ROUTE_INDEX_READY:
        await _ensure_route_index()

    if module not in _ROUTE_MODULES_WITH_COMMANDS:
        matcher_commands = _extract_matcher_command_literals(matcher_cls)
        if matcher_commands:
            for command in matcher_commands:
                if _command_matches(text, command):
                    return False, None
            if _matcher_has_alconna_shortcuts(matcher_cls):
                return False, None
            return True, "command_miss"
        return False, None

    route_modules = _get_route_modules_for_event(event, state)
    if module not in route_modules:
        if _matcher_has_alconna_shortcuts(matcher_cls):
            return False, None
        return True, "route_miss"
    return False, None


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
    _record_prefilter_stats(skip, reason)
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
    if _is_command_matcher_class(Matcher):
        module = _matcher_module_name(Matcher)
        if _is_heavy_command_module(module):
            async with HEAVY_COMMAND_SEMAPHORE:
                await original(**kwargs)
            return
        async with COMMAND_MATCHER_SEMAPHORE:
            await original(**kwargs)
        return
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


def _get_message_text(
    message: UniMsg | None,
    event_cache: dict | None,
    event: Event | None = None,
) -> str:
    if event_cache is not None:
        cached = event_cache.get("plain_text")
        if isinstance(cached, str):
            return cached

    text = ""
    if message is not None:
        with contextlib.suppress(Exception):
            text = message.extract_plain_text()
    if not text and event is not None:
        with contextlib.suppress(Exception):
            text = (event.get_plaintext() or "").strip()

    if event_cache is not None:
        event_cache["plain_text"] = text
    return text


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


async def start_auth_runtime_tasks() -> None:
    global _CACHE_SWEEP_TASK
    await _ensure_route_index()
    _install_matcher_prefilter()
    if _CACHE_SWEEP_TASK is None or _CACHE_SWEEP_TASK.done():
        _CACHE_SWEEP_TASK = asyncio.create_task(_cache_sweep_loop())


async def stop_auth_runtime_tasks() -> None:
    global _CACHE_SWEEP_TASK
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
    await DB_SEMAPHORE.acquire()
    async with DB_ACTIVE_LOCK:
        DB_ACTIVE_COUNT += 1
        _debug_log(f"current db auth concurrency: {DB_ACTIVE_COUNT}", LOGGER_COMMAND)
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            DB_SEMAPHORE.release()
        async with DB_ACTIVE_LOCK:
            DB_ACTIVE_COUNT = max(DB_ACTIVE_COUNT - 1, 0)
            _debug_log(
                f"current db auth concurrency: {DB_ACTIVE_COUNT}", LOGGER_COMMAND
            )


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


def _needs_auth_plugin(plugin: PluginInfo, group, entity) -> bool:
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
    session: Uninfo, entity, event_cache
) -> tuple[tuple[LevelUserSnapshot | None, LevelUserSnapshot | None] | None, bool]:
    if event_cache is not None and "admin_levels" in event_cache:
        return event_cache.get("admin_levels"), event_cache.get("admin_timeout", False)
    levels = await LevelUserMemoryCache.get_levels(session.user.id, entity.group_id)
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


async def _fetch_plugin(plugin_dao: DataAccess, module: str) -> PluginInfo | None:
    return await with_timeout(
        plugin_dao.safe_get_or_none(module=module), name="get_plugin"
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
                async with _db_section():
                    user = await _fetch_user_readonly(user_dao, user_id)
                user_cache[user_id] = user
        else:
            async with _db_section():
                user = await _fetch_user_readonly(user_dao, user_id)

    return plugin, user


async def get_plugin_cost(
    bot: Bot, user: UserConsole | None, plugin: PluginInfo, session: Uninfo
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
    cost_gold = await with_timeout(auth_cost(user, plugin, session), name="auth_cost")
    if session.user.id in bot.config.superusers:
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
    user_dao = DataAccess(UserConsole)
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
        logger.error(
            f"扣除金币超时，用户: {user_id}, 金币: {cost_gold}",
            LOGGER_COMMAND,
            session=session,
        )

    # 清除缓存，使下次查询时从数据库获取最新数据
    await user_dao.clear_cache(user_id=user_id)
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
    """尝试获取全局信号量并更新计数器，超时则抛出 PermissionExemption。"""
    global HOOKS_ACTIVE_COUNT
    await HOOKS_SEMAPHORE.acquire()
    async with HOOKS_ACTIVE_LOCK:
        HOOKS_ACTIVE_COUNT += 1
        _debug_log(
            (
                "当前并发权限检查数量: "
                f"{HOOKS_ACTIVE_COUNT}, limit={HOOKS_CONCURRENCY_LIMIT}"
            ),
            LOGGER_COMMAND,
        )


async def _leave_hooks_section():
    """释放信号量并更新计数器。"""
    global HOOKS_ACTIVE_COUNT
    with contextlib.suppress(Exception):
        HOOKS_SEMAPHORE.release()
    async with HOOKS_ACTIVE_LOCK:
        HOOKS_ACTIVE_COUNT = max(HOOKS_ACTIVE_COUNT - 1, 0)
        _debug_log(
            (
                "当前并发权限检查数量: "
                f"{HOOKS_ACTIVE_COUNT}, limit={HOOKS_CONCURRENCY_LIMIT}"
            ),
            LOGGER_COMMAND,
        )


async def auth_ban_fast(
    matcher: Matcher, event: Event, bot: Bot, session: Uninfo
) -> None:
    """快速 ban 检测（仅使用内存缓存），用于前置快速裁决。"""
    entity = get_entity_ids(session)
    event_cache = _get_event_cache(event, session, entity)
    if event_cache is not None and event_cache.get("ban_state") is True:
        raise SkipPluginException("user or group banned (cached)")
    if entity.user_id in bot.config.superusers:
        if event_cache is not None:
            event_cache["ban_state"] = False
        return
    if entity.group_id and await is_ban(None, entity.group_id):
        if event_cache is not None:
            event_cache["ban_state"] = True
        raise SkipPluginException("group banned (fast)")
    if entity.user_id and await is_ban(entity.user_id, entity.group_id):
        if event_cache is not None:
            event_cache["ban_state"] = True
        raise SkipPluginException("user banned (fast)")
    if event_cache is not None:
        event_cache["ban_state"] = False


async def route_precheck(
    matcher: Matcher,
    event: Event,
    session: Uninfo,
    message: UniMsg | None,
    *,
    entity=None,
    event_cache: dict | None = None,
    text: str | None = None,
    route_modules: set[str] | None = None,
) -> bool:
    module = matcher.plugin_name or ""
    if not module:
        return False
    if _is_hidden_plugin(matcher):
        return False
    if not _is_command_matcher_class(type(matcher)):
        return False
    if entity is None:
        entity = get_entity_ids(session)
    if event_cache is None:
        event_cache = _get_event_cache(event, session, entity)
    if text is None:
        text = _get_message_text(message, event_cache, event)
    if route_modules is None:
        route_modules = await _get_route_context(text, event_cache)
    if module in _ROUTE_MODULES_WITH_COMMANDS and module not in route_modules:
        if _matcher_has_alconna_shortcuts(type(matcher)):
            return False
        if event_cache is not None:
            event_cache["route_skip"] = True
        return True
    return False


async def auth_precheck(
    matcher: Matcher,
    event: Event,
    bot: Bot,
    session: Uninfo,
    message: UniMsg,
) -> None:
    """轻量前置检查：命令路由 + 必要管理员权限。"""
    module = matcher.plugin_name or ""
    if not module:
        return
    if _is_hidden_plugin(matcher):
        return
    entity = get_entity_ids(session)

    if session.user.id in bot.config.superusers:
        return

    plugin = cast(PluginInfo | None, await PluginInfoMemoryCache.get_by_module(module))
    if not plugin:
        return

    if plugin.plugin_type == PluginType.SUPERUSER:
        raise SkipPluginException("超级管理员权限不足...")

    if _needs_admin_check(plugin):
        await LevelUserMemoryCache.ensure_fresh()
        levels = await LevelUserMemoryCache.get_levels(session.user.id, entity.group_id)
        await auth_admin(plugin, session, cached_levels=levels)


async def _call_auth_ban_compat(
    matcher: Matcher,
    bot: Bot,
    session: Uninfo,
    plugin: PluginInfo,
    *,
    entity,
) -> None:
    """兼容旧签名 auth_ban(matcher, bot, session, plugin)。"""
    try:
        await auth_ban(matcher, bot, session, plugin, entity=entity)
    except TypeError as exc:
        if "unexpected keyword argument 'entity'" not in str(exc):
            raise
        await auth_ban(matcher, bot, session, plugin)


async def auth(
    matcher: Matcher,
    event: Event,
    bot: Bot,
    session: Uninfo,
    message: UniMsg | None,
    *,
    skip_ban: bool = False,
    entity=None,
    event_cache: dict | None = None,
    text: str | None = None,
    route_modules: set[str] | None = None,
    is_superuser: bool | None = None,
):
    """权限检查

    参数:
        matcher: matcher
        event: Event
        bot: bot
        session: Uninfo
        message: UniMsg
    """
    start_time = time.time()
    cost_gold = 0
    ignore_flag = False
    if entity is None:
        entity = get_entity_ids(session)
    if is_superuser is None:
        is_superuser = session.user.id in bot.config.superusers
    module = matcher.plugin_name or ""
    is_command_matcher = _is_command_matcher_class(type(matcher))
    if event_cache is None:
        event_cache = _get_event_cache(event, session, entity)
    auth_allowed = None
    auth_result_cache = None
    admin_checked_pre = False

    # 仅在慢请求时记录 hook 明细，避免热路径高频构造字符串
    hook_recorder = HookTraceRecorder(start_time)
    hooks_time = 0  # 初始化 hooks_time 变量

    # 记录是否已进入 hooks 区域（用于 finally 中释放）
    entered_hooks = False

    try:
        if not module:
            auth_allowed = True
            return

        if event_cache is not None:
            auth_result_cache = event_cache.setdefault("auth_result", {})
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

        if text is None:
            text = _get_message_text(message, event_cache, event)
        if route_modules is None:
            route_modules = await _get_route_context(text, event_cache)
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

        platform = PlatformUtils.get_platform(session)
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
                await LevelUserMemoryCache.ensure_fresh()
                admin_levels = None
                admin_timeout = False
                if event_cache is not None:
                    admin_levels, admin_timeout = await _get_admin_levels_cached(
                        session, entity, event_cache
                    )
                if admin_timeout:
                    hook_recorder.set("auth_admin", "timeout")
                else:
                    admin_start = time.time()
                    await auth_admin(plugin, session, cached_levels=admin_levels)
                    hook_recorder.set(
                        "auth_admin", f"{time.time() - admin_start:.3f}s(pre)"
                    )
                admin_checked_pre = True

        ban_cache_state = None
        if event_cache is not None:
            ban_cache_state = event_cache.get("ban_state")
        if skip_ban:
            if ban_cache_state is True:
                hook_recorder.set("auth_ban", "cached")
                raise SkipPluginException("user or group banned (cached)")
            if ban_cache_state is None:
                ban_start = time.time()
                try:
                    await _call_auth_ban_compat(
                        matcher, bot, session, plugin, entity=entity
                    )
                    hook_recorder.set("auth_ban", f"{time.time() - ban_start:.3f}s")
                    if event_cache is not None:
                        event_cache["ban_state"] = False
                except SkipPluginException:
                    hook_recorder.set("auth_ban", f"{time.time() - ban_start:.3f}s")
                    if event_cache is not None:
                        event_cache["ban_state"] = True
                    raise
            else:
                hook_recorder.set("auth_ban", "skipped")
        else:
            if ban_cache_state is True:
                hook_recorder.set("auth_ban", "cached")
                raise SkipPluginException("user or group banned (cached)")
            if ban_cache_state is None:
                ban_start = time.time()
                try:
                    await _call_auth_ban_compat(
                        matcher, bot, session, plugin, entity=entity
                    )
                    hook_recorder.set("auth_ban", f"{time.time() - ban_start:.3f}s")
                    if event_cache is not None:
                        event_cache["ban_state"] = False
                except SkipPluginException:
                    hook_recorder.set("auth_ban", f"{time.time() - ban_start:.3f}s")
                    if event_cache is not None:
                        event_cache["ban_state"] = True
                    raise
            else:
                hook_recorder.set("auth_ban", "cached")

        # 获取插件费用
        if not route_skip_checks and plugin.cost_gold > 0:
            cost_start = time.time()
            try:
                cost_gold = await with_timeout(
                    get_plugin_cost(bot, user, plugin, session), name="get_plugin_cost"
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
        bot_filter(session)

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
                session, entity, event_cache
            )

        # 并行执行所有 hook 检查，并记录执行时间
        hooks_start = time.time()
        allow_sleep_bypass = _is_bot_wake_command(module, text)

        # 创建所有 hook 任务
        hook_tasks = []
        if event_cache is None:
            hook_tasks.append(
                time_hook(
                    auth_bot(
                        plugin,
                        bot.self_id,
                        allow_sleep_bypass=allow_sleep_bypass,
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
                    auth_group(plugin, group, text, entity.group_id),
                    "auth_group",
                    hook_recorder,
                )
            )

        if not route_skip_checks and plugin.admin_level and not admin_checked_pre:
            if event_cache is None:
                hook_tasks.append(
                    time_hook(auth_admin(plugin, session), "auth_admin", hook_recorder)
                )
            else:
                if admin_timeout:
                    hook_recorder.set("auth_admin", "timeout")
                else:
                    hook_tasks.append(
                        time_hook(
                            auth_admin(plugin, session, cached_levels=admin_levels),
                            "auth_admin",
                            hook_recorder,
                        )
                    )
        else:
            hook_recorder.setdefault("auth_admin", "skipped")

        if is_superuser:
            hook_recorder.set("auth_plugin", "superuser")
        elif not route_skip_checks and _needs_auth_plugin(plugin, group, entity):
            hook_tasks.append(
                time_hook(
                    auth_plugin(
                        plugin,
                        group,
                        session,
                        event,
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
                    time_hook(auth_limit(plugin, session), "auth_limit", hook_recorder)
                )
            else:
                hook_recorder.set("auth_limit", "skipped")
        else:
            hook_recorder.set("auth_limit", "skipped")

        if hook_tasks:
            # 进入 hooks 并行检查区域（会在高并发时排队）
            await _enter_hooks_section()
            entered_hooks = True

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

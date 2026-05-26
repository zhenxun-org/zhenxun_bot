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
from zhenxun.services.auth_observability import (
    append_auth_decision_log,
    append_runtime_backpressure_log,
)
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
from zhenxun.services.message_load import is_overloaded, signal_overload
from zhenxun.utils.enum import BlockType, GoldHandle, PluginType
from zhenxun.utils.exception import InsufficientGold
from zhenxun.utils.platform import PlatformUtils

from .auth.auth_admin import auth_admin
from .auth.auth_ban import auth_ban
from .auth.auth_bot import auth_bot
from .auth.auth_cost import auth_cost
from .auth.auth_group import _is_group_wake_command, auth_group
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
from .auth_activation import (
    ActivationContext,
    HandlerActivationIndex,
)
from .auth_patch_guard import (
    validate_check_and_run_matcher_patch,
    validate_handle_event_patch,
)
from .auth_pipeline import AuthPipeline, AuthPipelineContext, AuthPipelineStage
from .auth_policy import (
    PolicyContext,
    PolicyDecisionPoint,
    action_from_snapshot,
    principal_from_snapshot,
    raise_for_policy,
    resource_from_snapshot,
)
from .auth_profile import PluginAuthProfile, get_plugin_auth_profile
from .auth_runtime_config import AUTH_DISPATCH_RUNTIME_CONFIG
from .auth_side_effect import SideEffectCommit
from .auth_snapshot import AuthSnapshot, build_auth_snapshot

AUTH_HOOKS_CONCURRENCY_LIMIT = AUTH_DISPATCH_RUNTIME_CONFIG.hooks_concurrency_limit
AUTH_DB_CONCURRENCY_LIMIT = AUTH_DISPATCH_RUNTIME_CONFIG.db_concurrency_limit
AUTH_DISPATCH_COMMAND_EXACT_LIMIT = AUTH_DISPATCH_RUNTIME_CONFIG.command_exact_limit
AUTH_DISPATCH_COMMAND_SHORTCUT_LIMIT = (
    AUTH_DISPATCH_RUNTIME_CONFIG.command_shortcut_limit
)
AUTH_DISPATCH_COMMAND_REGEX_LIMIT = AUTH_DISPATCH_RUNTIME_CONFIG.command_regex_limit
AUTH_DISPATCH_SYSTEM_LIMIT = AUTH_DISPATCH_RUNTIME_CONFIG.system_limit
AUTH_DISPATCH_PASSIVE_LIGHT_LIMIT = AUTH_DISPATCH_RUNTIME_CONFIG.passive_light_limit
AUTH_DISPATCH_PASSIVE_DB_LIMIT = AUTH_DISPATCH_RUNTIME_CONFIG.passive_db_limit
AUTH_DISPATCH_PASSIVE_HTTP_LIMIT = AUTH_DISPATCH_RUNTIME_CONFIG.passive_http_limit
AUTH_DISPATCH_PASSIVE_AI_LIMIT = AUTH_DISPATCH_RUNTIME_CONFIG.passive_ai_limit
AUTH_DISPATCH_PASSIVE_RENDER_LIMIT = AUTH_DISPATCH_RUNTIME_CONFIG.passive_render_limit
AUTH_DISPATCH_PLAIN_PASSIVE_LIGHT_LIMIT = (
    AUTH_DISPATCH_RUNTIME_CONFIG.plain_passive_light_limit
)
AUTH_DISPATCH_PLAIN_PASSIVE_DB_LIMIT = (
    AUTH_DISPATCH_RUNTIME_CONFIG.plain_passive_db_limit
)
AUTH_OVERLOAD_SELECTED_THRESHOLD = (
    AUTH_DISPATCH_RUNTIME_CONFIG.overload_selected_threshold
)
AUTH_OVERLOAD_LANE_WAIT_MS = AUTH_DISPATCH_RUNTIME_CONFIG.overload_lane_wait_ms


# 超时设置（秒）
TIMEOUT_SECONDS = AUTH_DISPATCH_RUNTIME_CONFIG.timeout_seconds
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
CIRCUIT_RESET_TIME = AUTH_DISPATCH_RUNTIME_CONFIG.circuit_reset_time

# 并发控制：限制同时进入 hooks 并行检查的协程数
HOOKS_CONCURRENCY_LIMIT = AUTH_HOOKS_CONCURRENCY_LIMIT
DB_CONCURRENCY_LIMIT = AUTH_DB_CONCURRENCY_LIMIT

# 路由索引缓存
_ROUTE_INDEX_LOCK = asyncio.Lock()
_ROUTE_INDEX_READY = False
_ROUTE_COMMAND_MAP: dict[str, set[str]] = {}
_ROUTE_PREFIX_MAP: dict[str, set[str]] = {}
_ROUTE_MODULES_WITH_COMMANDS: set[str] = set()
MATCHER_ROUTE_PREFILTER_TTL = AUTH_DISPATCH_RUNTIME_CONFIG.matcher_route_prefilter_ttl
PREFILTER_STATS_LOG_INTERVAL = AUTH_DISPATCH_RUNTIME_CONFIG.prefilter_stats_log_interval
CACHE_SWEEP_INTERVAL = AUTH_DISPATCH_RUNTIME_CONFIG.cache_sweep_interval
DISPATCH_STATS_LOG_INTERVAL = AUTH_DISPATCH_RUNTIME_CONFIG.dispatch_stats_log_interval

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
_HANDLER_ACTIVATION_INDEX = HandlerActivationIndex()
_AUTH_PDP = PolicyDecisionPoint()
_MATCHER_COMMAND_TYPE_CACHE: dict[type[Matcher], bool] = {}
_MATCHER_COMMAND_LITERAL_CACHE: dict[type[Matcher], tuple[str, ...] | None] = {}
_MATCHER_ALCONNA_SHORTCUT_CACHE: dict[type[Matcher], tuple[str, ...] | None] = {}
_MATCHER_RULE_DESCRIPTOR_CACHE: dict[type[Matcher], tuple["RuleDescriptor", ...]] = {}
_CHECK_MATCHER_ROUTE_CACHE = CacheDict(
    "AUTH_MATCHER_ROUTE_CACHE", expire=MATCHER_ROUTE_PREFILTER_TTL
)


@dataclass(slots=True)
class AuthPreparation:
    plugin: PluginInfo
    user: UserConsole | None
    profile: PluginAuthProfile
    snapshot: AuthSnapshot
    permission_context: PermissionContext
    policy_context: PolicyContext


@dataclass(slots=True)
class AuthPolicyFlags:
    bot_policy_done: bool = False
    group_policy_done: bool = False
    admin_policy_done: bool = False
    admin_checked_pre: bool = False
    plugin_policy_done: bool = False
    should_return_allowed: bool = False


@dataclass(slots=True)
class AuthLaneContext:
    lane: str = "passive_light"
    scope_key: str = ""
    queue_size: int = 0

    @property
    def is_guaranteed(self) -> bool:
        return self.lane.startswith("command_") or self.lane == "system"


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


@dataclass(frozen=True, slots=True)
class RuleDescriptor:
    kind: str
    value: object | None = None
    flags: int = 0
    ignorecase: bool = False
    deterministic_text: bool = False
    command_like: bool = False


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


def _rule_call_name(call) -> str:
    call_type = call.__class__
    return getattr(call_type, "__name__", "")


def _rule_call_module(call) -> str:
    call_type = call.__class__
    return getattr(call_type, "__module__", "")


def _normalize_rule_string_tuple(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple | set | frozenset):
        result = tuple(str(item) for item in value if str(item))
        return result
    return ()


def _iter_matcher_rule_calls(matcher_cls: type[Matcher]):
    rule = getattr(matcher_cls, "rule", None)
    checkers = getattr(rule, "checkers", ()) or ()
    for checker in checkers:
        call = getattr(checker, "call", None)
        if call is not None:
            yield call


def _extract_matcher_rule_descriptors(
    matcher_cls: type[Matcher],
) -> tuple[RuleDescriptor, ...]:
    if matcher_cls in _MATCHER_RULE_DESCRIPTOR_CACHE:
        return _MATCHER_RULE_DESCRIPTOR_CACHE[matcher_cls]

    descriptors: list[RuleDescriptor] = []
    if hasattr(matcher_cls, "command"):
        descriptors.append(RuleDescriptor("matcher_command", command_like=True))

    for call in _iter_matcher_rule_calls(matcher_cls):
        call_module = _rule_call_module(call)
        call_name = _rule_call_name(call)
        if call_module.startswith("nonebot.rule"):
            if call_name == "CommandRule":
                descriptors.append(
                    RuleDescriptor(
                        "command",
                        getattr(call, "cmds", ()),
                        command_like=True,
                    )
                )
            elif call_name == "ShellCommandRule":
                descriptors.append(
                    RuleDescriptor(
                        "shell_command",
                        getattr(call, "cmds", ()),
                        command_like=True,
                    )
                )
            elif call_name == "RegexRule":
                descriptors.append(
                    RuleDescriptor(
                        "regex",
                        getattr(call, "regex", ""),
                        flags=int(getattr(call, "flags", 0) or 0),
                        deterministic_text=True,
                        command_like=True,
                    )
                )
            elif call_name == "StartswithRule":
                descriptors.append(
                    RuleDescriptor(
                        "startswith",
                        _normalize_rule_string_tuple(getattr(call, "msg", ())),
                        ignorecase=bool(getattr(call, "ignorecase", False)),
                        deterministic_text=True,
                        command_like=True,
                    )
                )
            elif call_name == "EndswithRule":
                descriptors.append(
                    RuleDescriptor(
                        "endswith",
                        _normalize_rule_string_tuple(getattr(call, "msg", ())),
                        ignorecase=bool(getattr(call, "ignorecase", False)),
                        deterministic_text=True,
                        command_like=True,
                    )
                )
            elif call_name == "FullmatchRule":
                descriptors.append(
                    RuleDescriptor(
                        "fullmatch",
                        _normalize_rule_string_tuple(getattr(call, "msg", ())),
                        ignorecase=bool(getattr(call, "ignorecase", False)),
                        deterministic_text=True,
                        command_like=True,
                    )
                )
            elif call_name == "KeywordsRule":
                descriptors.append(
                    RuleDescriptor(
                        "keywords",
                        _normalize_rule_string_tuple(getattr(call, "keywords", ())),
                        deterministic_text=True,
                        command_like=True,
                    )
                )
            elif call_name == "IsTypeRule":
                descriptors.append(
                    RuleDescriptor("is_type", getattr(call, "types", ()))
                )
            elif call_name == "ToMeRule":
                descriptors.append(RuleDescriptor("to_me"))
            else:
                descriptors.append(RuleDescriptor("custom"))
        elif (
            call_module.startswith("nonebot_plugin_alconna.rule")
            and call_name == "AlconnaRule"
        ):
            descriptors.append(RuleDescriptor("alconna", command_like=True))
        else:
            descriptors.append(RuleDescriptor("custom"))

    result = tuple(descriptors)
    _MATCHER_RULE_DESCRIPTOR_CACHE[matcher_cls] = result
    return result


def _matcher_has_command_like_rule(matcher_cls: type[Matcher]) -> bool:
    return any(
        descriptor.command_like
        for descriptor in _extract_matcher_rule_descriptors(matcher_cls)
    )


def _matcher_has_deterministic_text_rule(matcher_cls: type[Matcher]) -> bool:
    return any(
        descriptor.deterministic_text
        for descriptor in _extract_matcher_rule_descriptors(matcher_cls)
    )


def _matcher_rule_matches_text(
    matcher_cls: type[Matcher],
    event: Event,
    plain_text: str,
) -> bool | None:
    matched_any = False
    saw_deterministic = False
    message_text: str | None = None

    for descriptor in _extract_matcher_rule_descriptors(matcher_cls):
        kind = descriptor.kind
        if kind == "regex":
            saw_deterministic = True
            pattern = str(descriptor.value or "")
            if not pattern:
                continue
            if message_text is None:
                with contextlib.suppress(Exception):
                    message_text = str(event.get_message())
                if message_text is None:
                    message_text = plain_text
            try:
                if re.search(pattern, message_text, descriptor.flags):
                    matched_any = True
                else:
                    return False
            except re.error:
                return False
        elif kind == "startswith":
            saw_deterministic = True
            text = plain_text.casefold() if descriptor.ignorecase else plain_text
            prefixes = descriptor.value if isinstance(descriptor.value, tuple) else ()
            candidates = (
                tuple(item.casefold() for item in prefixes)
                if descriptor.ignorecase
                else prefixes
            )
            if any(text.startswith(prefix) for prefix in candidates if prefix):
                matched_any = True
            else:
                return False
        elif kind == "endswith":
            saw_deterministic = True
            text = plain_text.casefold() if descriptor.ignorecase else plain_text
            suffixes = descriptor.value if isinstance(descriptor.value, tuple) else ()
            candidates = (
                tuple(item.casefold() for item in suffixes)
                if descriptor.ignorecase
                else suffixes
            )
            if any(text.endswith(suffix) for suffix in candidates if suffix):
                matched_any = True
            else:
                return False
        elif kind == "fullmatch":
            saw_deterministic = True
            text = plain_text.casefold() if descriptor.ignorecase else plain_text
            values = descriptor.value if isinstance(descriptor.value, tuple) else ()
            candidates = (
                tuple(item.casefold() for item in values)
                if descriptor.ignorecase
                else values
            )
            if text in candidates:
                matched_any = True
            else:
                return False
        elif kind == "keywords":
            saw_deterministic = True
            keywords = descriptor.value if isinstance(descriptor.value, tuple) else ()
            if any(keyword and keyword in plain_text for keyword in keywords):
                matched_any = True
            else:
                return False
        elif kind == "is_type":
            types = descriptor.value
            if isinstance(types, type):
                if not isinstance(event, types):
                    return False
            elif isinstance(types, tuple) and types:
                if not isinstance(event, types):
                    return False
        elif kind == "to_me":
            if not getattr(event, "to_me", False):
                return False

    if matched_any:
        return True
    if saw_deterministic:
        return False
    return None


def _is_command_matcher_class(matcher_cls: type[Matcher]) -> bool:
    if matcher_cls in _MATCHER_COMMAND_TYPE_CACHE:
        return _MATCHER_COMMAND_TYPE_CACHE[matcher_cls]
    result = _matcher_has_command_like_rule(matcher_cls)
    _MATCHER_COMMAND_TYPE_CACHE[matcher_cls] = result
    return result


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
    if _matcher_has_deterministic_text_rule(matcher_cls):
        return "command_regex"
    commands = _extract_matcher_command_literals(matcher_cls) or ()
    if any(_is_regex_like_command_literal(command) for command in commands):
        return "command_regex"
    return "command_exact"


def _dispatch_budget_for_context(context: EventDispatchContext) -> dict[str, int]:
    high_signal = (
        context.to_me or context.is_command_like or context.has_url or context.has_image
    )
    passive_light = (
        AUTH_DISPATCH_PASSIVE_LIGHT_LIMIT
        if high_signal
        else AUTH_DISPATCH_PLAIN_PASSIVE_LIGHT_LIMIT
    )
    passive_db = (
        AUTH_DISPATCH_PASSIVE_DB_LIMIT
        if high_signal
        else AUTH_DISPATCH_PLAIN_PASSIVE_DB_LIMIT
    )
    budget = {
        "passive_light": passive_light,
        "passive_db": passive_db,
        "passive_http": AUTH_DISPATCH_PASSIVE_HTTP_LIMIT if context.has_url else 0,
        "passive_ai": AUTH_DISPATCH_PASSIVE_AI_LIMIT
        if (context.to_me or context.is_command_like)
        else int(bool(context.plain_text)),
        "passive_render": AUTH_DISPATCH_PASSIVE_RENDER_LIMIT
        if (context.to_me or context.has_image or context.is_command_like)
        else 0,
    }
    return budget


def _activation_context_from_dispatch(
    context: EventDispatchContext,
    event: Event,
) -> ActivationContext:
    return ActivationContext(
        event=event,
        event_type=context.event_type,
        plain_text=context.plain_text,
        raw_text=context.raw_text,
        to_me=context.to_me,
        has_url=context.has_url,
        has_image=context.has_image,
        is_command_like=context.is_command_like,
        route_modules=set(context.route_modules),
        ai_route_modules=set(context.ai_route_modules),
        ai_route_heads=set(context.ai_route_heads),
    )


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


def _auth_scope_key(context: EventContext) -> str:
    group_id = context.group_id or ""
    channel_id = context.channel_id or ""
    message_id = context.message_id if context.message_id is not None else ""
    return (
        f"{context.platform}:{context.bot_id}:"
        f"{context.user_id}:{group_id}:{channel_id}:{message_id}"
    )


def _auth_lane_context_from_state(
    matcher_cls: type[Matcher],
    auth_context: EventContext,
    state: dict | None,
) -> AuthLaneContext:
    dispatch_context = None
    if state is not None:
        value = state.get("_zx_dispatch_context")
        if isinstance(value, EventDispatchContext):
            dispatch_context = value
    if dispatch_context is None:
        dispatch_context = EventDispatchContext(
            event_type=auth_context.event_type,
            plain_text=auth_context.plain_text,
            is_command_like=bool(auth_context.route_modules),
            route_modules=set(auth_context.route_modules),
        )
    lane = _dispatch_lane_for_matcher(matcher_cls, dispatch_context)
    semaphore = _DISPATCH_LANE_SEMAPHORES.get(lane)
    queue_size = 0
    if semaphore is not None:
        limit = _DISPATCH_LANE_LIMITS.get(lane, 0)
        value = getattr(semaphore, "_value", limit)
        queue_size = max(limit - int(value), 0)
    return AuthLaneContext(
        lane=lane,
        scope_key=_auth_scope_key(auth_context),
        queue_size=queue_size,
    )


@contextlib.asynccontextmanager
async def _dispatch_lane_section(lane: str):
    semaphore = _DISPATCH_LANE_SEMAPHORES.get(lane)
    if semaphore is None:
        yield
        return
    started = time.perf_counter()
    await semaphore.acquire()
    wait_ms = (time.perf_counter() - started) * 1000
    if wait_ms >= AUTH_OVERLOAD_LANE_WAIT_MS:
        signal_overload(2.0)
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


def _collect_alconna_shortcut_strings(command, depth: int = 0) -> set[str]:
    shortcuts: set[str] = set()
    if command is None or depth > 4:
        return shortcuts
    get_shortcuts = getattr(command, "get_shortcuts", None)
    if callable(get_shortcuts):
        with contextlib.suppress(Exception):
            raw_shortcuts = get_shortcuts()
            if isinstance(raw_shortcuts, list | tuple | set | frozenset):
                for shortcut in raw_shortcuts:
                    if isinstance(shortcut, str) and shortcut.strip():
                        shortcuts.add(shortcut.strip())
    elif callable(command):
        with contextlib.suppress(Exception):
            resolved = command()
            if resolved is not None and resolved is not command:
                shortcuts.update(_collect_alconna_shortcut_strings(resolved, depth + 1))
                return shortcuts

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
    for attr in ("command", "commands", "base", "formatter", "source"):
        nested = getattr(command, attr, None)
        if nested is not None and nested is not command:
            shortcuts.update(_collect_alconna_shortcut_strings(nested, depth + 1))
    return shortcuts


def _extract_matcher_alconna_shortcuts(
    matcher_cls: type[Matcher],
) -> tuple[str, ...] | None:
    if matcher_cls in _MATCHER_ALCONNA_SHORTCUT_CACHE:
        return _MATCHER_ALCONNA_SHORTCUT_CACHE[matcher_cls]

    shortcuts: set[str] = set()
    for attr in ("command", "_rule", "rule"):
        shortcuts.update(
            _collect_alconna_shortcut_strings(getattr(matcher_cls, attr, None))
        )
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
    text = re.sub(r"^\[(?:[^\]]*)\]\s*", "", text)
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
    rule_match = _matcher_rule_matches_text(matcher_cls, event, text)
    if rule_match is True:
        command_matched = True
    elif rule_match is False and not command_matched:
        return True, "command_miss"
    elif not (matcher_commands or shortcut_match is not None) and not command_matched:
        return True, "command_miss"

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
    rule_match = _matcher_rule_matches_text(matcher_cls, event, text)
    if rule_match is True:
        command_matched = True
    elif rule_match is False and not command_matched:
        return True, "command_miss"
    elif not (matcher_commands or shortcut_match is not None) and not command_matched:
        return True, "command_miss"

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
    guard = validate_check_and_run_matcher_patch()
    if not guard.ok:
        logger.warning(
            "权限 matcher 预筛选 patch 未安装，回退 NoneBot 原生分发: "
            f"{guard.reason}",
            LOGGER_COMMAND,
        )
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
        activation_context = _activation_context_from_dispatch(
            dispatch_context,
            event,
        )
        activation_available = True
        try:
            _HANDLER_ACTIVATION_INDEX.ensure_fresh(matchers)
        except Exception as exc:
            activation_available = False
            logger.warning(
                "HandlerActivationIndex 构建失败，回退到旧 matcher 选择逻辑",
                LOGGER_COMMAND,
                e=exc,
            )

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
                if activation_available:
                    try:
                        activation_result = _HANDLER_ACTIVATION_INDEX.select_priority(
                            priority,
                            priority_matchers,
                            activation_context,
                            dispatch_budget.copy(),
                        )
                    except Exception as exc:
                        logger.warning(
                            "HandlerActivationIndex 选择失败，当前 priority 回退",
                            LOGGER_COMMAND,
                            e=exc,
                        )
                        activation_result = None
                else:
                    activation_result = None

                if activation_result is not None:
                    selected_matchers = activation_result.selected
                    deterministic_selected = activation_result.deterministic_selected
                    for lane, count in activation_result.skipped_by_lane.items():
                        for _ in range(count):
                            _record_dispatch_selection(lane, False)
                    if (
                        activation_result.candidate_count
                        > AUTH_OVERLOAD_SELECTED_THRESHOLD
                    ):
                        signal_overload(3.0)
                else:
                    selected_matchers = priority_matchers
                    deterministic_selected = set()

                async with anyio_mod.create_task_group() as tg:
                    for matcher in selected_matchers:
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
                        ignore_budget = matcher in deterministic_selected
                        if (
                            lane.startswith("passive_")
                            and not ignore_budget
                            and not _consume_dispatch_budget(lane, dispatch_budget)
                        ):
                            _record_dispatch_selection(lane, False)
                            await append_runtime_backpressure_log(
                                scope_key=f"{bot.type}:{bot.self_id}",
                                reason="dispatch_passive_budget_exhausted",
                                lane=lane,
                                action="skip",
                                queue_size=len(selected_matchers),
                                active_count=HOOKS_ACTIVE_COUNT,
                            )
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
    guard = validate_handle_event_patch()
    if not guard.ok:
        logger.warning(
            "权限事件分发选择器 patch 未安装，回退 NoneBot 原生分发: "
            f"{guard.reason}",
            LOGGER_COMMAND,
        )
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
                _MATCHER_RULE_DESCRIPTOR_CACHE,
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


async def _has_limits_cached(
    module: str,
    event_cache: dict | None,
    *,
    known: bool | None = None,
) -> bool:
    module_limit_cache: dict[str, bool] = {}
    if event_cache is not None:
        module_limit_cache = event_cache.setdefault("module_limits", {})
    if module in module_limit_cache:
        return module_limit_cache[module]
    if known is not None:
        module_limit_cache[module] = known
        return known
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


def _policy_skip_message(reason: str) -> str:
    return {
        "user_or_group_banned": "user or group banned (cached)",
        "superuser_required": "超级管理员权限不足...",
        "bot_not_found": "Bot不存在，阻断权限检测...",
        "bot_sleeping": "Bot休眠中阻断权限检测...",
        "bot_plugin_blocked": "Bot插件权限检查结果为关闭...",
        "group_not_found": "群组信息不存在...",
        "group_blacklisted": "群组黑名单, 目标群组群权限权限-1...",
        "group_sleeping": "群组休眠状态...",
        "group_level_low": "群等级限制...",
        "admin_level_low": "管理员权限不足...",
        "plugin_disabled_in_group": "该插件在群组中已被禁用...",
        "plugin_superuser_blocked_in_group": "超级管理员禁用了该群此功能...",
        "plugin_blocked_in_group": "该群未开启此功能...",
        "plugin_disabled_in_private": "该插件在私聊中已被禁用...",
        "plugin_global_disabled": "全局未开启此功能...",
    }.get(reason, reason or "permission denied")


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


async def _record_backpressure(
    *,
    lane_context: AuthLaneContext,
    reason: str,
    action: str,
    duration_ms: float = 0.0,
) -> None:
    await append_runtime_backpressure_log(
        scope_key=lane_context.scope_key,
        reason=reason,
        lane=lane_context.lane,
        action=action,
        queue_size=lane_context.queue_size,
        active_count=HOOKS_ACTIVE_COUNT,
        duration_ms=duration_ms,
    )


async def _enter_hooks_section(lane_context: AuthLaneContext):
    """尝试获取全局信号量；过载时保命令、降级被动 matcher。"""
    global HOOKS_ACTIVE_COUNT
    if HOOKS_SEMAPHORE.locked():
        signal_overload(3.0)
        action = "execute" if lane_context.is_guaranteed else "defer"
        await _record_backpressure(
            lane_context=lane_context,
            reason="hooks_semaphore_saturated",
            action=action,
        )
        if not lane_context.is_guaranteed:
            logger.warning(
                "hooks semaphore saturated, passive matcher deferred",
                LOGGER_COMMAND,
            )
            raise SkipPluginException("hooks saturated passive deferred")
        logger.warning(
            "hooks semaphore saturated, guaranteed lane waiting",
            LOGGER_COMMAND,
        )
    started = time.perf_counter()
    await HOOKS_SEMAPHORE.acquire()
    wait_ms = (time.perf_counter() - started) * 1000
    if wait_ms >= AUTH_OVERLOAD_LANE_WAIT_MS:
        signal_overload(2.0)
        await _record_backpressure(
            lane_context=lane_context,
            reason="hooks_wait_slow",
            action="execute",
            duration_ms=wait_ms,
        )
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


async def _prepare_auth_state(
    *,
    module: str,
    context: EventContext,
    bot: Bot,
    event_cache: dict | None,
    route_skip_checks: bool,
    skip_ban: bool,
    hook_recorder: HookTraceRecorder,
    state: dict | None,
    session: Uninfo,
) -> AuthPreparation | None:
    entity = context.entity
    plugin_user_start = time.time()
    try:
        plugin, user = await with_timeout(
            get_plugin_and_user(
                module,
                entity.user_id,
                context.platform,
                event_cache=event_cache,
                need_user=not route_skip_checks,
            ),
            name="get_plugin_and_user",
        )
        hook_recorder.set("get_plugin_user", f"{time.time() - plugin_user_start:.3f}s")
    except asyncio.TimeoutError:
        logger.error(
            f"获取插件和用户数据超时，模块: {module}",
            LOGGER_COMMAND,
            session=session,
        )
        return None

    permission_context = PermissionContext(
        event=context,
        module=module,
        plugin=plugin,
        user=user,
    )
    store_permission_context(state, permission_context)

    profile = await get_plugin_auth_profile(plugin, event_cache=event_cache)
    snapshot = await build_auth_snapshot(
        context=context,
        plugin=plugin,
        profile=profile,
        bot=bot,
        skip_ban=skip_ban,
    )
    permission_context.group = snapshot.group
    permission_context.bot_data = snapshot.bot_data
    if snapshot.admin_levels is not None:
        permission_context.admin_levels = snapshot.admin_levels
    store_permission_context(state, permission_context)

    policy_context = PolicyContext(
        snapshot=snapshot,
        route_skip_checks=route_skip_checks,
        allow_sleep_bypass=_is_bot_wake_command(module, context.plain_text),
        allow_group_sleep_bypass=_is_group_wake_command(plugin, context.plain_text),
    )
    return AuthPreparation(
        plugin=plugin,
        user=user,
        profile=profile,
        snapshot=snapshot,
        permission_context=permission_context,
        policy_context=policy_context,
    )


def _apply_policy_precheck(
    prep: AuthPreparation,
    hook_recorder: HookTraceRecorder,
) -> AuthPolicyFlags:
    flags = AuthPolicyFlags()
    snapshot = prep.snapshot
    decision = _AUTH_PDP.decide(
        principal_from_snapshot(snapshot),
        action_from_snapshot(snapshot),
        resource_from_snapshot(snapshot),
        prep.policy_context,
    )
    if decision.denied:
        raise_for_policy(decision, _policy_skip_message(decision.reason))
    if decision.allowed and decision.reason in {
        "hidden_plugin_skip_auth",
        "route_miss_skip_checks",
    }:
        flags.should_return_allowed = True
        return flags

    bot_decision = _AUTH_PDP.decide_bot(prep.policy_context)
    if bot_decision.allowed:
        flags.bot_policy_done = True
        hook_recorder.set("auth_bot", "policy")
    elif bot_decision.denied:
        raise_for_policy(bot_decision, _policy_skip_message(bot_decision.reason))

    group_decision = _AUTH_PDP.decide_group(prep.policy_context)
    if group_decision.allowed or group_decision.skipped:
        flags.group_policy_done = True
        hook_recorder.set("auth_group", f"policy:{group_decision.reason}")
    elif group_decision.denied:
        raise_for_policy(group_decision, _policy_skip_message(group_decision.reason))

    plugin_decision = _AUTH_PDP.decide_plugin(prep.policy_context)
    if plugin_decision.allowed or plugin_decision.skipped:
        flags.plugin_policy_done = True
        hook_recorder.set("auth_plugin", f"policy:{plugin_decision.reason}")
    elif plugin_decision.denied:
        raise_for_policy(plugin_decision, _policy_skip_message(plugin_decision.reason))

    return flags


async def _precheck_admin_policy(
    *,
    prep: AuthPreparation,
    flags: AuthPolicyFlags,
    event_cache: dict | None,
    route_skip_checks: bool,
    hook_recorder: HookTraceRecorder,
    session: Uninfo,
) -> None:
    plugin = prep.plugin
    permission_context = prep.permission_context
    entity = prep.permission_context.entity
    if route_skip_checks or not _needs_admin_check(plugin):
        return
    if plugin.plugin_type in {PluginType.SUPERUSER, PluginType.SUPER_AND_ADMIN}:
        if prep.snapshot.is_superuser:
            hook_recorder.set("auth_admin", "superuser")
            flags.admin_checked_pre = True
        elif plugin.plugin_type == PluginType.SUPERUSER:
            raise SkipPluginException("超级管理员权限不足...")

    admin_decision = _AUTH_PDP.decide_admin(prep.policy_context)
    if admin_decision.allowed or admin_decision.skipped:
        flags.admin_checked_pre = True
        flags.admin_policy_done = True
        hook_recorder.set("auth_admin", f"policy:{admin_decision.reason}")
    elif admin_decision.denied:
        raise_for_policy(admin_decision, _policy_skip_message(admin_decision.reason))
    if flags.admin_checked_pre:
        return

    if event_cache is not None and event_cache.get("admin_precheck_done"):
        hook_recorder.set("auth_admin", "precheck")
        flags.admin_checked_pre = True
        return

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
        await auth_admin(plugin, session, context=permission_context)
        hook_recorder.set("auth_admin", f"{time.time() - admin_start:.3f}s(pre)")
    flags.admin_checked_pre = True


async def _check_ban_from_snapshot(
    *,
    prep: AuthPreparation,
    matcher: Matcher,
    event_cache: dict | None,
    skip_ban: bool,
    hook_recorder: HookTraceRecorder,
    session: Uninfo,
) -> None:
    ban_cache_state = prep.snapshot.ban_state
    if event_cache is not None:
        ban_cache_state = event_cache.get("ban_state")
    if ban_cache_state is True:
        hook_recorder.set("auth_ban", "cached")
        raise SkipPluginException("user or group banned (cached)")
    if ban_cache_state is False:
        hook_recorder.set("auth_ban", "cached")
        return
    if skip_ban:
        hook_recorder.set("auth_ban", "skipped")
        return

    ban_start = time.time()
    try:
        await auth_ban(
            matcher,
            session,
            prep.plugin,
            context=prep.permission_context,
        )
        hook_recorder.set("auth_ban", f"{time.time() - ban_start:.3f}s")
        if event_cache is not None:
            event_cache["ban_state"] = False
    except SkipPluginException:
        hook_recorder.set("auth_ban", f"{time.time() - ban_start:.3f}s")
        if event_cache is not None:
            event_cache["ban_state"] = True
        raise


async def _resolve_cost_gold(
    *,
    prep: AuthPreparation,
    route_skip_checks: bool,
    hook_recorder: HookTraceRecorder,
    session: Uninfo,
) -> int:
    plugin = prep.plugin
    if route_skip_checks or prep.profile.cost_gold <= 0:
        hook_recorder.set("cost_gold", "skipped")
        return 0
    cost_start = time.time()
    try:
        cost_gold = await with_timeout(
            get_plugin_cost(
                prep.user,
                plugin,
                session,
                context=prep.permission_context,
            ),
            name="get_plugin_cost",
        )
        hook_recorder.set("cost_gold", f"{time.time() - cost_start:.3f}s")
        return cost_gold
    except asyncio.TimeoutError:
        logger.error(
            f"获取插件费用超时，模块: {prep.profile.module}",
            LOGGER_COMMAND,
            session=session,
        )
        return 0


async def _refresh_permission_context_from_snapshot(
    *,
    prep: AuthPreparation,
    event_cache: dict | None,
    bot: Bot,
    flags: AuthPolicyFlags,
    route_skip_checks: bool,
    state: dict | None,
) -> tuple[
    GroupSnapshot | None,
    BotSnapshot | None,
    bool,
    tuple[LevelUserSnapshot | None, LevelUserSnapshot | None] | None,
    bool,
]:
    entity = prep.permission_context.entity
    group: GroupSnapshot | None = prep.snapshot.group
    if group is None:
        group = await _get_group_cached(entity, event_cache)

    bot_data: BotSnapshot | None = prep.snapshot.bot_data
    bot_timeout = bool(event_cache.get("bot_timeout", False)) if event_cache else False
    if event_cache is not None and bot_data is None and "bot_data" not in event_cache:
        bot_data, bot_timeout = await _get_bot_data_cached(bot.self_id, event_cache)

    admin_levels = prep.snapshot.admin_levels
    admin_timeout = False
    if (
        not flags.admin_checked_pre
        and prep.profile.admin_level
        and event_cache is not None
        and not route_skip_checks
        and admin_levels is None
    ):
        admin_levels, admin_timeout = await _get_admin_levels_cached(
            entity, event_cache
        )

    prep.permission_context.group = group
    prep.permission_context.bot_data = bot_data
    if admin_levels is not None:
        prep.permission_context.admin_levels = admin_levels
    store_permission_context(state, prep.permission_context)
    return group, bot_data, bot_timeout, admin_levels, admin_timeout


async def _run_auth_hooks(
    *,
    prep: AuthPreparation,
    matcher: Matcher,
    event: Event,
    bot: Bot,
    session: Uninfo,
    event_cache: dict | None,
    route_skip_checks: bool,
    flags: AuthPolicyFlags,
    lane_context: AuthLaneContext,
    hook_recorder: HookTraceRecorder,
    state: dict | None,
) -> float:
    plugin = prep.plugin
    profile = prep.profile
    context = prep.permission_context
    side_effect_commit = SideEffectCommit(session=session, module=profile.module)
    entity = context.entity
    is_superuser = prep.snapshot.is_superuser
    text = prep.policy_context.snapshot.context.plain_text
    hooks_start = time.time()
    (
        group,
        bot_data,
        bot_timeout,
        _,
        admin_timeout,
    ) = await _refresh_permission_context_from_snapshot(
        prep=prep,
        event_cache=event_cache,
        bot=bot,
        flags=flags,
        route_skip_checks=route_skip_checks,
        state=state,
    )

    await _enter_hooks_section(lane_context)
    hook_tasks = []
    try:
        if not flags.bot_policy_done:
            if event_cache is None:
                hook_tasks.append(
                    time_hook(
                        auth_bot(
                            plugin,
                            bot.self_id,
                            allow_sleep_bypass=prep.policy_context.allow_sleep_bypass,
                            context=context,
                        ),
                        "auth_bot",
                        hook_recorder,
                    )
                )
            elif bot_timeout:
                hook_recorder.set("auth_bot", "timeout")
            else:
                hook_tasks.append(
                    time_hook(
                        auth_bot(
                            plugin,
                            bot.self_id,
                            bot_data=bot_data,
                            skip_fetch=True,
                            allow_sleep_bypass=prep.policy_context.allow_sleep_bypass,
                            context=context,
                        ),
                        "auth_bot",
                        hook_recorder,
                    )
                )

        if not flags.group_policy_done:
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
                            context=context,
                        ),
                        "auth_group",
                        hook_recorder,
                    )
                )

        if flags.admin_policy_done:
            pass
        elif (
            not route_skip_checks and plugin.admin_level and not flags.admin_checked_pre
        ):
            if event_cache is None:
                hook_tasks.append(
                    time_hook(
                        auth_admin(plugin, session, context=context),
                        "auth_admin",
                        hook_recorder,
                    )
                )
            elif admin_timeout:
                hook_recorder.set("auth_admin", "timeout")
            else:
                hook_tasks.append(
                    time_hook(
                        auth_admin(plugin, session, context=context),
                        "auth_admin",
                        hook_recorder,
                    )
                )
        else:
            hook_recorder.setdefault("auth_admin", "skipped")

        if not flags.plugin_policy_done:
            if is_superuser:
                hook_recorder.set("auth_plugin", "superuser")
            elif not route_skip_checks and _needs_auth_plugin(plugin, context):
                hook_tasks.append(
                    time_hook(
                        auth_plugin(
                            plugin,
                            group,
                            session,
                            event,
                            context=context,
                            skip_group_block=is_superuser,
                        ),
                        "auth_plugin",
                        hook_recorder,
                    )
                )
            else:
                hook_recorder.set("auth_plugin", "skipped")

        if not route_skip_checks:
            has_limits = await _has_limits_cached(
                profile.module,
                event_cache,
                known=profile.has_limit,
            )
            if has_limits:
                hook_tasks.append(
                    time_hook(
                        side_effect_commit.commit_limit(
                            lambda: auth_limit(plugin, session, context=context)
                        ),
                        "auth_limit",
                        hook_recorder,
                    )
                )
            else:
                hook_recorder.set("auth_limit", "skipped")
        else:
            hook_recorder.set("auth_limit", "skipped")

        try:
            await with_timeout(
                asyncio.gather(*hook_tasks),
                timeout=TIMEOUT_SECONDS * 2,
                name="auth_hooks_gather",
            )
        except asyncio.TimeoutError:
            logger.error(
                f"权限检查 hooks 总体执行超时，模块: {profile.module}",
                LOGGER_COMMAND,
                session=session,
            )
    finally:
        await _leave_hooks_section()
    return time.time() - hooks_start


async def _auth_pipeline_route_gate(ctx: AuthPipelineContext) -> None:
    if not ctx.module:
        ctx.stop(allowed=True, effect="allow", reason="empty_module")
        return

    ctx.side_effect_lock = ctx.side_effect_cache.lock_for(ctx.module)
    await ctx.side_effect_lock.acquire()
    ctx.entered_side_effect_lock = True

    ctx.auth_result_cache = ctx.side_effect_cache.auth_results
    cached_result = ctx.auth_result_cache.get(ctx.module)
    if cached_result is not None:
        allowed, reason = cached_result
        if not allowed:
            ctx.decision_effect = "skip"
            ctx.decision_reason = reason or "auth_cached_skip"
            raise SkipPluginException(reason or "auth cached skip")
        ctx.stop(allowed=True, effect="allow", reason="auth_cached_allow")
        return

    if _is_hidden_plugin(ctx.matcher):
        ctx.stop(allowed=True, effect="allow", reason="hidden_plugin")
        return
    if ctx.event_cache is not None and ctx.event_cache.get("ban_state") is True:
        ctx.decision_effect = "skip"
        ctx.decision_reason = "ban_cached"
        raise SkipPluginException("user or group banned (cached)")

    if ctx.route_modules is None:
        ctx.route_modules = await _get_route_context(ctx.text, ctx.event_cache)
        set_route_modules(ctx.state, ctx.event_context, ctx.route_modules)
    ctx.route_skip_checks = (
        ctx.is_command_matcher
        and ctx.module in _ROUTE_MODULES_WITH_COMMANDS
        and ctx.module not in ctx.route_modules
        and not _matcher_has_alconna_shortcuts(type(ctx.matcher))
    )
    if ctx.route_skip_checks:
        if ctx.event_cache is not None:
            ctx.event_cache["route_skip"] = True
        ctx.hook_recorder.set("route", "miss")
        ctx.stop(allowed=True, effect="allow", reason="route_miss_skip_checks")


async def _auth_pipeline_prepare_snapshot(ctx: AuthPipelineContext) -> None:
    ctx.prep = await _prepare_auth_state(
        module=ctx.module,
        context=ctx.event_context,
        bot=ctx.bot,
        event_cache=ctx.event_cache,
        route_skip_checks=ctx.route_skip_checks,
        skip_ban=ctx.skip_ban,
        hook_recorder=ctx.hook_recorder,
        state=ctx.state,
        session=ctx.session,
    )
    if ctx.prep is None:
        ctx.stop(allowed=True, effect="allow", reason="prepare_timeout_allow")


async def _auth_pipeline_policy_precheck(ctx: AuthPipelineContext) -> None:
    ctx.flags = _apply_policy_precheck(ctx.prep, ctx.hook_recorder)
    if ctx.flags.should_return_allowed:
        ctx.stop(allowed=True, effect="allow", reason="policy_precheck_allow")
        return
    await _precheck_admin_policy(
        prep=ctx.prep,
        flags=ctx.flags,
        event_cache=ctx.event_cache,
        route_skip_checks=ctx.route_skip_checks,
        hook_recorder=ctx.hook_recorder,
        session=ctx.session,
    )
    await _check_ban_from_snapshot(
        prep=ctx.prep,
        matcher=ctx.matcher,
        event_cache=ctx.event_cache,
        skip_ban=ctx.skip_ban,
        hook_recorder=ctx.hook_recorder,
        session=ctx.session,
    )
    ctx.cost_gold = await _resolve_cost_gold(
        prep=ctx.prep,
        route_skip_checks=ctx.route_skip_checks,
        hook_recorder=ctx.hook_recorder,
        session=ctx.session,
    )


async def _auth_pipeline_legacy_hook_adapter(ctx: AuthPipelineContext) -> None:
    bot_filter(ctx.session, context=ctx.prep.permission_context)
    ctx.hooks_time = await _run_auth_hooks(
        prep=ctx.prep,
        matcher=ctx.matcher,
        event=ctx.event,
        bot=ctx.bot,
        session=ctx.session,
        event_cache=ctx.event_cache,
        route_skip_checks=ctx.route_skip_checks,
        flags=ctx.flags,
        lane_context=ctx.lane_context,
        hook_recorder=ctx.hook_recorder,
        state=ctx.state,
    )
    ctx.auth_allowed = True
    ctx.decision_effect = "allow"
    ctx.decision_reason = "auth_passed"


async def _auth_pipeline_side_effect_commit(ctx: AuthPipelineContext) -> None:
    if ctx.ignore_flag or ctx.cost_gold <= 0:
        if ctx.ignore_flag:
            await ctx.side_effect_commit.rollback_gold("auth_ignored")
        return
    gold_start = time.time()
    try:
        await ctx.side_effect_commit.reserve_gold(
            lambda: reduce_gold(
                ctx.entity.user_id,
                ctx.module,
                ctx.cost_gold,
                ctx.session,
            ),
            amount=ctx.cost_gold,
            metadata={"module": ctx.module},
        )
        await with_timeout(
            ctx.side_effect_commit.commit_gold(),
            name="reduce_gold",
        )
        ctx.hook_recorder.set("reduce_gold", f"{time.time() - gold_start:.3f}s")
    except asyncio.TimeoutError:
        logger.error(
            f"扣除金币超时，模块: {ctx.module}",
            LOGGER_COMMAND,
            session=ctx.session,
        )


async def _auth_pipeline_decision_log(ctx: AuthPipelineContext) -> None:
    if ctx.auth_result_cache is not None and ctx.auth_allowed is not None:
        ctx.auth_result_cache[ctx.module] = (
            ctx.auth_allowed,
            None if ctx.auth_allowed else ctx.decision_reason,
        )
    if ctx.entered_side_effect_lock and ctx.side_effect_lock is not None:
        with contextlib.suppress(Exception):
            ctx.side_effect_lock.release()
        ctx.entered_side_effect_lock = False
    latency_ms = (time.time() - ctx.start_time) * 1000
    await append_auth_decision_log(
        bot_id=ctx.event_context.bot_id,
        platform=ctx.event_context.platform,
        group_id=ctx.entity.group_id,
        user_id=ctx.entity.user_id,
        module=ctx.module,
        effect=ctx.decision_effect or "error",
        reason=ctx.decision_reason,
        latency_ms=latency_ms,
        overloaded=is_overloaded(),
    )


_AUTH_PIPELINE = AuthPipeline(
    [
        AuthPipelineStage("route_gate", _auth_pipeline_route_gate),
        AuthPipelineStage("prepare_snapshot", _auth_pipeline_prepare_snapshot),
        AuthPipelineStage("policy_precheck", _auth_pipeline_policy_precheck),
        AuthPipelineStage("legacy_hook_adapter", _auth_pipeline_legacy_hook_adapter),
    ]
)


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
    entity = context.entity
    event_cache = context.event_cache
    text = context.plain_text
    route_modules = context.route_modules if context.route_modules_loaded else None
    module = matcher.plugin_name or ""
    is_command_matcher = _is_command_matcher_class(type(matcher))
    lane_context = _auth_lane_context_from_state(type(matcher), context, state)
    side_effect_cache = get_permission_side_effect_cache(
        state=state,
        event_cache=event_cache,
    )
    side_effect_commit = SideEffectCommit(session=session, module=module)

    # 仅在慢请求时记录 hook 明细，避免热路径高频构造字符串
    hook_recorder = HookTraceRecorder(start_time)
    pipeline_context = AuthPipelineContext(
        matcher=matcher,
        event=event,
        bot=bot,
        session=session,
        event_context=context,
        skip_ban=skip_ban,
        state=state,
        start_time=start_time,
        module=module,
        entity=entity,
        event_cache=event_cache,
        text=text,
        route_modules=route_modules,
        is_command_matcher=is_command_matcher,
        lane_context=lane_context,
        side_effect_cache=side_effect_cache,
        side_effect_commit=side_effect_commit,
        hook_recorder=hook_recorder,
    )

    try:
        await _AUTH_PIPELINE.run(pipeline_context)

    except SkipPluginException as e:
        LimitManager.unblock(module, entity.user_id, entity.group_id, entity.channel_id)
        if e.tip_message:
            await side_effect_commit.send_permission_tip(
                e.tip_message,
                e.tip_check_tag,
                background=e.tip_background,
                timeout=e.tip_timeout,
            )
        logger.info(str(e), LOGGER_COMMAND, session=session)
        pipeline_context.ignore_flag = True
        pipeline_context.auth_allowed = False
        pipeline_context.decision_effect = "defer" if "deferred" in str(e) else "skip"
        pipeline_context.decision_reason = str(e) or "skip_plugin"
    except IsSuperuserException:
        logger.debug("超级用户跳过权限检测...", LOGGER_COMMAND, session=session)
        pipeline_context.auth_allowed = True
        pipeline_context.decision_effect = "allow"
        pipeline_context.decision_reason = "superuser"
    except PermissionExemption as e:
        logger.info(str(e), LOGGER_COMMAND, session=session)
        pipeline_context.auth_allowed = True
        pipeline_context.decision_effect = "allow"
        pipeline_context.decision_reason = str(e) or "permission_exemption"
    finally:
        await _auth_pipeline_decision_log(pipeline_context)

    await _auth_pipeline_side_effect_commit(pipeline_context)

    # 记录总执行时间
    total_time = time.time() - start_time
    if total_time > WARNING_THRESHOLD:  # 如果总时间超过500ms，记录详细信息
        logger.warning(
            f"权限检查耗时过长: {total_time:.3f}s, 模块: {module}, "
            f"hooks时间: {pipeline_context.hooks_time:.3f}s, "
            f"详情: {hook_recorder.snapshot()}",
            LOGGER_COMMAND,
            session=session,
        )

    if pipeline_context.ignore_flag:
        raise IgnoredException("权限检测 ignore")

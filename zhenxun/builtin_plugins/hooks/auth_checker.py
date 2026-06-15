import asyncio
import contextlib
import re
import time
from typing import cast

from nonebot import get_loaded_plugins
from nonebot.adapters import Bot, Event
from nonebot.consts import CMD_ARG_KEY, CMD_KEY, PREFIX_KEY, RAW_CMD_KEY
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
import nonebot.message as nb_message
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.user_console import UserConsole
from zhenxun.services.cache.cache_containers import CacheDict
from zhenxun.services.log import logger
from zhenxun.services.message_load import signal_overload
from zhenxun.utils.enum import GoldHandle, PluginType
from zhenxun.utils.exception import InsufficientGold
from zhenxun.utils.platform import PlatformUtils

from .auth.auth_ban import auth_ban
from .auth.auth_cost import auth_cost
from .auth.auth_group import _is_group_wake_command
from .auth.auth_limit import LimitManager, reserve_auth_limit
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
from .auth.data_provider import DEFAULT_PERMISSION_DATA_PROVIDER
from .auth.exception import (
    IsSuperuserException,
    PermissionExemption,
    SkipPluginException,
)
from .auth_activation import (
    ActivationContext,
    HandlerActivationIndex,
    classify_matcher_lane,
    extract_matcher_alconna_shortcuts,
    text_match_candidates,
)
from .auth_event_selector import (
    HandleEventSelectorDependencies,
    install_handle_event_selector,
    uninstall_handle_event_selector,
)
from .auth_legacy_fallback import legacy_pure_auth_fallback
from .auth_pipeline import (
    AuthPipelineContext,
    AuthPipelineDependencies,
    build_auth_pipeline,
    decision_log_stage,
)
from .auth_policy import (
    PolicyContext,
    PolicyDecisionPoint,
)
from .auth_profile import get_plugin_auth_profile
from .auth_runtime_config import AUTH_DISPATCH_RUNTIME_CONFIG
from .auth_side_effect import SideEffectCommit
from .auth_snapshot import get_or_build_auth_snapshot
from .auth_trace import HookTraceRecorder
from .auth_types import (
    AuthLaneContext,
    AuthPreparation,
    EventDispatchContext,
)

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
AUTH_OVERLOAD_SELECTED_THRESHOLD = (
    AUTH_DISPATCH_RUNTIME_CONFIG.overload_selected_threshold
)
AUTH_OVERLOAD_LANE_WAIT_MS = AUTH_DISPATCH_RUNTIME_CONFIG.overload_lane_wait_ms


# 超时设置（秒）
TIMEOUT_SECONDS = AUTH_DISPATCH_RUNTIME_CONFIG.timeout_seconds
# 熔断计数器
CIRCUIT_BREAKERS = {
    "auth_ban": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_limit": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
    "auth_hooks_gather": {
        "failures": 0,
        "threshold": 3,
        "active": False,
        "reset_time": 0,
    },
    "get_plugin_cost": {
        "failures": 0,
        "threshold": 3,
        "active": False,
        "reset_time": 0,
    },
    "get_plugin_and_user": {
        "failures": 0,
        "threshold": 3,
        "active": False,
        "reset_time": 0,
    },
    "reserve_gold": {"failures": 0, "threshold": 3, "active": False, "reset_time": 0},
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
CACHE_SWEEP_INTERVAL = AUTH_DISPATCH_RUNTIME_CONFIG.cache_sweep_interval

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
_DISPATCH_BUDGET_LANES = set(_DISPATCH_LANE_LIMITS)
_HANDLER_ACTIVATION_INDEX = HandlerActivationIndex()
_AUTH_PDP = PolicyDecisionPoint()
_MATCHER_COMMAND_TYPE_CACHE: dict[type[Matcher], bool] = {}
_CHECK_MATCHER_ROUTE_CACHE = CacheDict(
    "AUTH_MATCHER_ROUTE_CACHE", expire=MATCHER_ROUTE_PREFILTER_TTL
)


_CACHE_SWEEP_TASK: asyncio.Task | None = None
_BOT_WAKE_COMMAND_PATTERN = re.compile(r"^bot醒来(?:\s+\S+)?$", re.IGNORECASE)
_BOT_WAKE_CANONICAL_PATTERN = re.compile(
    r"^bot_manage\s+bot_switch\s+enable(?:\s+\S+)?$", re.IGNORECASE
)
_URL_PATTERN = re.compile(r"(?:https?://|www\.|b23\.tv|t\.cn/)", re.IGNORECASE)


def _normalize_command(command: str) -> str:
    text = command.strip()
    if not text:
        return ""
    text = re.sub(r"^(?:\s*(?:\[[^\]]*]|\<[^>]*>))+\s*", "", text)
    cut_points = [idx for idx in (text.find("["), text.find("<")) if idx >= 0]
    if cut_points:
        text = text[: min(cut_points)]
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"(?:\s+[?*]+|[?*]+)$", "", text).strip()


def _split_command_variants(command: str) -> tuple[str, ...]:
    text = command.strip()
    if not text:
        return ()
    if text.startswith("/"):
        return (text,)
    if "/" in text and " " not in text:
        parts = tuple(part.strip() for part in text.split("/") if part.strip())
        if parts:
            return parts
    return (text,)


def _is_ambiguous_route_command(command: str) -> bool:
    text = command.strip()
    if not text:
        return True
    if any(token in text for token in ("?", "*", "|", "(", ")", "^", "$", "re:")):
        return True
    return "xx" in text.lower()


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
            if not command_set or has_ambiguous:
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


def _route_command_matches(text: str, command: str) -> bool:
    if not text or not command:
        return False
    if text == command:
        return True
    if text.startswith(command):
        if len(text) == len(command):
            return True
        return text[len(command)].isspace()
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
        if _route_command_matches(text, command):
            modules = _ROUTE_COMMAND_MAP.get(command)
            if modules:
                matched_modules.update(modules)
    return matched_modules


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


def _is_command_matcher_class(matcher_cls: type[Matcher]) -> bool:
    if matcher_cls in _MATCHER_COMMAND_TYPE_CACHE:
        return _MATCHER_COMMAND_TYPE_CACHE[matcher_cls]
    descriptor = _HANDLER_ACTIVATION_INDEX.descriptor_for(matcher_cls)
    if descriptor is not None:
        result = descriptor.command_like
    else:
        from .auth_activation import matcher_is_command_like

        result = matcher_is_command_like(matcher_cls)
    _MATCHER_COMMAND_TYPE_CACHE[matcher_cls] = result
    return result


def _matcher_has_alconna_shortcuts(matcher_cls: type[Matcher]) -> bool:
    descriptor = _HANDLER_ACTIVATION_INDEX.descriptor_for(matcher_cls)
    if descriptor is not None:
        return bool(descriptor.shortcuts)
    return bool(extract_matcher_alconna_shortcuts(matcher_cls))


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


def _normalize_dispatch_text(text: str) -> str:
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


def _message_to_plain_text(message: object) -> str:
    if message is None:
        return ""
    with contextlib.suppress(Exception):
        extractor = getattr(message, "extract_plain_text", None)
        if callable(extractor):
            return _normalize_dispatch_text(str(extractor() or ""))
    return _normalize_dispatch_text(str(message))


def _trie_command_text_from_state(state: dict | None) -> str:
    if state is None:
        return ""
    prefix = state.get(PREFIX_KEY)
    if not isinstance(prefix, dict):
        return ""
    command = prefix.get(CMD_KEY)
    if isinstance(command, tuple):
        return _normalize_dispatch_text(" ".join(str(item) for item in command))
    if isinstance(command, str):
        return _normalize_dispatch_text(command)
    return ""


def _trie_raw_command_from_state(state: dict | None) -> str:
    if state is None:
        return ""
    prefix = state.get(PREFIX_KEY)
    if not isinstance(prefix, dict):
        return ""
    raw_command = prefix.get(RAW_CMD_KEY)
    return _normalize_dispatch_text(raw_command) if isinstance(raw_command, str) else ""


def _trie_command_arg_text_from_state(state: dict | None) -> str:
    if state is None:
        return ""
    prefix = state.get(PREFIX_KEY)
    if not isinstance(prefix, dict):
        return ""
    return _message_to_plain_text(prefix.get(CMD_ARG_KEY))


def _event_text_candidates(
    event: Event,
    state: dict | None,
    plain_text: str = "",
    raw_text: str = "",
) -> tuple[str, ...]:
    candidates: list[str] = []

    def add(text: object) -> None:
        if not isinstance(text, str):
            return
        normalized = _normalize_dispatch_text(text)
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    add(_trie_command_text_from_state(state))
    trie_raw = _trie_raw_command_from_state(state)
    add(trie_raw)
    trie_arg = _trie_command_arg_text_from_state(state)
    if trie_raw and trie_arg:
        add(f"{trie_raw} {trie_arg}")
    add(plain_text)
    if event is not None:
        with contextlib.suppress(Exception):
            getter = getattr(event, "get_plaintext", None)
            if callable(getter):
                add(getter())
    add(raw_text)
    return tuple(candidates)


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
    text_candidates = _event_text_candidates(event, state, plain_text, raw_text)
    trie_command_text = _trie_command_text_from_state(state)
    trie_raw_command = _trie_raw_command_from_state(state)
    to_me = _event_to_me(event)
    has_url = _event_has_url(raw_text) or _event_has_url(plain_text)
    has_image = _event_has_image(event)
    is_command_like = bool(
        route_modules
        or ai_route_modules
        or trie_command_text
        or trie_raw_command
        or plain_text.startswith("/")
        or plain_text.startswith("!")
        or plain_text.startswith(".")
    )
    context = EventDispatchContext(
        event_type=event_type,
        plain_text=plain_text,
        raw_text=raw_text,
        trie_command_text=trie_command_text,
        trie_raw_command=trie_raw_command,
        text_candidates=text_candidates,
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
            or context.trie_command_text
            or context.trie_raw_command
            or context.plain_text.startswith("/")
            or context.plain_text.startswith("!")
            or context.plain_text.startswith(".")
        )
    return context


def _dispatch_lane_for_matcher(
    matcher_cls: type[Matcher], context: EventDispatchContext
) -> str:
    descriptor = _HANDLER_ACTIVATION_INDEX.descriptor_for(matcher_cls)
    if descriptor is not None:
        return descriptor.lane

    event_type = context.event_type
    if getattr(matcher_cls, "temp", False):
        return "system"
    matcher_type = getattr(matcher_cls, "type", "") or ""
    if isinstance(matcher_type, str) and matcher_type and matcher_type != event_type:
        return "system"
    return classify_matcher_lane(
        matcher_cls,
        ai_route_modules=context.ai_route_modules,
    )


def _activation_context_from_dispatch(
    context: EventDispatchContext,
    event: Event,
) -> ActivationContext:
    return ActivationContext(
        event=event,
        event_type=context.event_type,
        plain_text=context.text_candidates[0]
        if context.text_candidates
        else context.plain_text,
        raw_text="\n".join(context.text_candidates)
        if context.text_candidates
        else context.raw_text,
        to_me=context.to_me,
        has_url=context.has_url,
        has_image=context.has_image,
        is_command_like=context.is_command_like,
        route_modules=set(context.route_modules),
        ai_route_modules=set(context.ai_route_modules),
        ai_route_heads=set(context.ai_route_heads),
    )


def _new_dispatch_budget() -> dict[str, int]:
    return dict(_DISPATCH_LANE_LIMITS)


def _merge_dispatch_budget(
    target: dict[str, int],
    source: dict[str, int],
) -> None:
    for lane in _DISPATCH_BUDGET_LANES:
        target[lane] = source.get(lane, target.get(lane, 0))


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
            text_candidates=(auth_context.plain_text,)
            if auth_context.plain_text
            else (),
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
        raw_text = _event_raw_message_text(event)
        plain_text = _state_plain_text(state) or _event_plain_text(event)
        route_modules = set()
        for text in _event_text_candidates(event, state, plain_text, raw_text):
            route_modules.update(_match_route_modules(text))
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
    raw_text = _event_raw_message_text(event)
    text_candidates = _event_text_candidates(
        event,
        state,
        _event_plain_text(event),
        raw_text,
    )
    if text_candidates:
        state["_zx_text_candidates"] = text_candidates
    if _state_plain_text(state):
        return
    text = _event_plain_text(event)
    if text:
        state[STATE_PLAIN_TEXT] = text


def _build_matcher_state(base_state: dict) -> dict:
    # 第一次调用即在 base_state 写入副作用缓存对象;copy() 后 matcher_state
    # 与之共享同一引用,无需二次 get(B7)。
    get_permission_side_effect_cache(state=base_state)
    matcher_state = base_state.copy()
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


_MAX_MATCHER_CACHE = 512


_SELECTOR_DEPS = HandleEventSelectorDependencies(
    activation_index=_HANDLER_ACTIVATION_INDEX,
    overload_selected_threshold=AUTH_OVERLOAD_SELECTED_THRESHOLD,
    prepare_handle_event_state=_prepare_handle_event_state,
    build_dispatch_context=_build_dispatch_context,
    activation_context_from_dispatch=_activation_context_from_dispatch,
    new_dispatch_budget=_new_dispatch_budget,
    dispatch_lane_for_matcher=_dispatch_lane_for_matcher,
    merge_dispatch_budget=_merge_dispatch_budget,
    build_matcher_state=_build_matcher_state,
    run_selected_matcher=_run_selected_matcher,
)


def _install_handle_event_selector() -> None:
    install_handle_event_selector(_SELECTOR_DEPS)


def _uninstall_handle_event_selector() -> None:
    uninstall_handle_event_selector()


async def _get_route_context(text: str, event_cache: dict | None) -> set[str]:
    if not text:
        return set()
    if event_cache is not None and "route_modules" in event_cache:
        return event_cache["route_modules"]
    await _ensure_route_index()
    matched = set()
    for candidate in text_match_candidates(text):
        matched.update(_match_route_modules(candidate))
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
            for _mc in (_MATCHER_COMMAND_TYPE_CACHE,):
                if len(_mc) > _MAX_MATCHER_CACHE:
                    _mc.clear()


async def start_auth_runtime_tasks() -> None:
    global _CACHE_SWEEP_TASK
    await _ensure_route_index()
    _install_handle_event_selector()
    if _CACHE_SWEEP_TASK is None or _CACHE_SWEEP_TASK.done():
        _CACHE_SWEEP_TASK = asyncio.create_task(_cache_sweep_loop())


async def stop_auth_runtime_tasks() -> None:
    global _CACHE_SWEEP_TASK
    _uninstall_handle_event_selector()
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
        ready_cache = (
            event_cache.setdefault("module_limits_ready", {})
            if event_cache is not None
            else {}
        )
        if ready_cache.get(module, True):
            return module_limit_cache[module]
        if known is True:
            module_limit_cache[module] = True
            ready_cache[module] = True
            return True
    elif known is not None:
        module_limit_cache[module] = known
        if event_cache is not None:
            event_cache.setdefault("module_limits_ready", {})[module] = True
        return module_limit_cache[module]
    limit_entries = None
    if event_cache is not None:
        entry_cache = event_cache.setdefault("module_limit_entries", {})
        if module in entry_cache:
            limit_entries = entry_cache[module]
    if limit_entries is not None:
        has_limits = bool(limit_entries)
        module_limit_cache[module] = has_limits
        if event_cache is not None:
            event_cache.setdefault("module_limits_ready", {})[module] = True
        return has_limits
    provider = DEFAULT_PERMISSION_DATA_PROVIDER
    limit_entries = provider.get_module_limits_if_ready(module)
    if limit_entries is not None:
        has_limits = bool(limit_entries)
        module_limit_cache[module] = has_limits
        if event_cache is not None:
            event_cache.setdefault("module_limit_entries", {})[module] = limit_entries
            event_cache.setdefault("module_limits_ready", {})[module] = True
        return has_limits
    limits = await LimitManager.get_module_limits(module)
    has_limits = bool(limits)
    module_limit_cache[module] = has_limits
    if event_cache is not None:
        event_cache.setdefault("module_limit_entries", {})[module] = limits
        event_cache.setdefault("module_limits_ready", {})[module] = True
    return has_limits


@contextlib.asynccontextmanager
async def _db_section():
    """Legacy bounded DB section kept for explicit fallback callers."""
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


_POLICY_SKIP_MESSAGES = {
    "user_or_group_banned": "user or group banned (cached)",
    "superuser_required": "超级管理员权限不足...",
    "admin_required": "管理员权限不足...",
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
}


def _policy_skip_message(reason: str) -> str:
    return _POLICY_SKIP_MESSAGES.get(reason, reason or "permission denied")


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


async def _get_plugin_cache_first(
    module: str,
    event_cache: dict | None,
    *,
    allow_cache_load: bool,
) -> tuple[PluginInfo | None, bool]:
    provider = DEFAULT_PERMISSION_DATA_PROVIDER
    plugin = None
    if event_cache is not None:
        plugin_cache = event_cache.setdefault("plugin_cache", {})
        if module in plugin_cache:
            return cast(PluginInfo | None, plugin_cache[module]), False

    plugin = provider.get_plugin_if_ready(module)
    cache_miss = plugin is None and not provider.plugin_cache_loaded()
    if plugin is None and allow_cache_load:
        plugin = await provider.get_plugin(module)
        cache_miss = False
    if event_cache is not None:
        event_cache.setdefault("plugin_cache", {})[module] = plugin
        event_cache.setdefault("auth_cache_misses", set()).discard("plugin")
        if cache_miss:
            event_cache.setdefault("auth_cache_misses", set()).add("plugin")
    return plugin, cache_miss


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


async def reserve_gold(
    user_id: str,
    module: str,
    cost_gold: int,
    session: Uninfo,
):
    """预扣金币，matcher 未实际完成时由 SideEffectCommit 回滚。"""
    try:
        reservation = await with_timeout(
            UserConsole.reserve_gold(
                user_id,
                cost_gold,
                GoldHandle.PLUGIN,
                module,
                PlatformUtils.get_platform(session),
            ),
            name="reserve_gold",
        )
    except InsufficientGold:
        raise
    logger.debug(f"预扣功能花费金币: {cost_gold}", LOGGER_COMMAND, session=session)
    return reservation


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
    logger.debug(
        "auth backpressure: "
        f"scope={lane_context.scope_key}, lane={lane_context.lane}, "
        f"reason={reason}, action={action}, queue={lane_context.queue_size}, "
        f"active={HOOKS_ACTIVE_COUNT}, duration_ms={duration_ms:.1f}",
        LOGGER_COMMAND,
    )


async def _enter_hooks_section(lane_context: AuthLaneContext):
    """尝试获取全局信号量；过载时记录背压但不丢弃 matcher。"""
    global HOOKS_ACTIVE_COUNT
    if HOOKS_SEMAPHORE.locked():
        signal_overload(3.0)
        await _record_backpressure(
            lane_context=lane_context,
            reason="hooks_semaphore_saturated",
            action="wait",
        )
        logger.warning(
            "hooks semaphore saturated, matcher waiting",
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


async def _prepare_auth_state(
    *,
    module: str,
    context: EventContext,
    bot: Bot,
    event_cache: dict | None,
    skip_ban: bool,
    hook_recorder: HookTraceRecorder,
    state: dict | None,
    session: Uninfo,
    allow_cache_load: bool = False,
) -> AuthPreparation | None:
    plugin_user_start = time.time()
    try:
        plugin, plugin_cache_miss = await _get_plugin_cache_first(
            module,
            event_cache,
            allow_cache_load=allow_cache_load,
        )
        user = None
        if plugin is None:
            if not allow_cache_load and plugin_cache_miss:
                return None
            raise PermissionExemption(
                f"plugin:{module} not found, skip permission check"
            )
        if plugin.plugin_type == PluginType.HIDDEN:
            raise PermissionExemption(
                f"plugin {plugin.name}:{plugin.module} hidden, skip"
            )
        hook_recorder.set("get_plugin_user", f"{time.time() - plugin_user_start:.3f}s")
    except asyncio.TimeoutError:
        logger.error(
            f"获取插件和用户数据超时，模块: {module}",
            LOGGER_COMMAND,
            session=session,
        )
        return None
    except PermissionExemption:
        raise

    permission_context = PermissionContext(
        event=context,
        module=module,
        plugin=plugin,
        user=user,
    )
    store_permission_context(state, permission_context)

    profile = await get_plugin_auth_profile(
        plugin,
        event_cache=event_cache,
        allow_cache_load=allow_cache_load,
    )
    snapshot = await get_or_build_auth_snapshot(
        context=context,
        plugin=plugin,
        profile=profile,
        bot=bot,
        skip_ban=skip_ban,
        allow_cache_load=allow_cache_load,
    )
    permission_context.group = snapshot.group
    permission_context.bot_data = snapshot.bot_data
    if snapshot.admin_levels is not None:
        permission_context.admin_levels = snapshot.admin_levels
    store_permission_context(state, permission_context)

    policy_context = PolicyContext(
        snapshot=snapshot,
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


async def _prepare_auth_state_with_fallback(
    *,
    module: str,
    context: EventContext,
    bot: Bot,
    event_cache: dict | None,
    skip_ban: bool,
    hook_recorder: HookTraceRecorder,
    state: dict | None,
    session: Uninfo,
) -> AuthPreparation | None:
    prep = await _prepare_auth_state(
        module=module,
        context=context,
        bot=bot,
        event_cache=event_cache,
        skip_ban=skip_ban,
        hook_recorder=hook_recorder,
        state=state,
        session=session,
        allow_cache_load=False,
    )
    if prep is not None:
        return prep
    hook_recorder.set("auth_snapshot", "cache_miss_fallback")
    return await _prepare_auth_state(
        module=module,
        context=context,
        bot=bot,
        event_cache=event_cache,
        skip_ban=skip_ban,
        hook_recorder=hook_recorder,
        state=state,
        session=session,
        allow_cache_load=True,
    )


async def _check_ban_from_snapshot(
    *,
    prep: AuthPreparation,
    matcher: Matcher,
    event_cache: dict | None,
    skip_ban: bool,
    hook_recorder: HookTraceRecorder,
    session: Uninfo,
) -> None:
    # skip_ban 上移到 cached 判断之前(A7),否则豁免参数对 cached 命中形同虚设。
    if skip_ban:
        hook_recorder.set("auth_ban", "skipped")
        return
    is_superuser = bool(getattr(prep.permission_context, "is_superuser", False))
    ban_cache_state = prep.snapshot.ban_state
    if event_cache is not None:
        ban_cache_state = event_cache.get("ban_state")
    if ban_cache_state is True:
        # 超级用户豁免(A7):与 PDP / 旧轨权威路径保持一致,避免被 ban 后无法自救。
        if is_superuser:
            hook_recorder.set("auth_ban", "cached_superuser_exempt")
            return
        hook_recorder.set("auth_ban", "cached")
        raise SkipPluginException("user or group banned (cached)")
    if ban_cache_state is False:
        hook_recorder.set("auth_ban", "cached")
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


async def _reserve_limit_side_effect(
    *,
    prep: AuthPreparation,
    session: Uninfo,
    side_effect_commit: SideEffectCommit,
) -> None:
    reservation = await reserve_auth_limit(
        prep.plugin,
        session,
        context=prep.permission_context,
    )
    await side_effect_commit.reserve_limit(reservation)


async def _resolve_cost_gold(
    *,
    prep: AuthPreparation,
    hook_recorder: HookTraceRecorder,
    session: Uninfo,
) -> int:
    plugin = prep.plugin
    if prep.profile.cost_gold <= 0:
        hook_recorder.set("cost_gold", "skipped")
        return 0
    cost_start = time.time()
    try:
        if prep.user is None:
            user_start = time.time()
            prep.user = await with_timeout(
                UserConsole.get_user(
                    prep.permission_context.user_id,
                    PlatformUtils.get_platform(session),
                ),
                name="get_cost_user",
            )
            prep.permission_context.user = prep.user
            hook_recorder.set("get_cost_user", f"{time.time() - user_start:.3f}s")
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


async def _run_auth_hooks(
    *,
    prep: AuthPreparation,
    session: Uninfo,
    event_cache: dict | None,
    lane_context: AuthLaneContext,
    hook_recorder: HookTraceRecorder,
    side_effect_commit: SideEffectCommit,
) -> float:
    profile = prep.profile
    hooks_start = time.time()

    await _enter_hooks_section(lane_context)
    hook_tasks = []
    try:
        has_limits = await _has_limits_cached(
            profile.module,
            event_cache,
            known=profile.has_limit,
        )
        if has_limits:
            hook_tasks.append(
                time_hook(
                    _reserve_limit_side_effect(
                        prep=prep,
                        session=session,
                        side_effect_commit=side_effect_commit,
                    ),
                    "auth_limit",
                    hook_recorder,
                )
            )
        else:
            hook_recorder.set("auth_limit", "skipped")

        if not hook_tasks:
            return time.time() - hooks_start

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


_AUTH_PIPELINE_DEPS = AuthPipelineDependencies(
    route_modules_with_commands=_ROUTE_MODULES_WITH_COMMANDS,
    get_route_context=_get_route_context,
    is_hidden_plugin=_is_hidden_plugin,
    is_command_matcher_class=_is_command_matcher_class,
    matcher_has_alconna_shortcuts=_matcher_has_alconna_shortcuts,
    prepare_auth_state_with_fallback=_prepare_auth_state_with_fallback,
    prepare_auth_state=_prepare_auth_state,
    policy_decision_point=_AUTH_PDP,
    policy_skip_message=_policy_skip_message,
    legacy_pure_auth_fallback=legacy_pure_auth_fallback,
    check_ban_from_snapshot=_check_ban_from_snapshot,
    resolve_cost_gold=_resolve_cost_gold,
    run_auth_hooks=_run_auth_hooks,
    bot_filter=bot_filter,
    reserve_gold=reserve_gold,
    insufficient_gold_error=InsufficientGold,
    logger=logger,
    log_command=LOGGER_COMMAND,
)
_AUTH_PIPELINE = build_auth_pipeline(_AUTH_PIPELINE_DEPS)


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
    side_effect_commit = SideEffectCommit(
        session=session,
        module=module,
        owner_matcher_id=id(matcher),
        limit_entity=entity,
    )

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
        await side_effect_commit.rollback_all("auth_skip")
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
        await side_effect_commit.rollback_all("permission_exemption")
        logger.info(str(e), LOGGER_COMMAND, session=session)
        pipeline_context.auth_allowed = True
        pipeline_context.decision_effect = "allow"
        pipeline_context.decision_reason = str(e) or "permission_exemption"
    except Exception:
        await side_effect_commit.rollback_all("auth_exception")
        raise
    finally:
        await decision_log_stage(pipeline_context, _AUTH_PIPELINE_DEPS)

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

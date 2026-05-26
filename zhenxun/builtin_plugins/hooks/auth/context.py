from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nonebot.adapters import Bot, Event
from nonebot_plugin_alconna import UniMsg
from nonebot_plugin_uninfo import Uninfo

from zhenxun.services.cache.cache_containers import CacheDict
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.utils import EntityIDs, get_entity_ids

AUTH_EVENT_CACHE_TTL = 5

STATE_EVENT_CONTEXT = "_zx_event_context"
STATE_PERMISSION_CONTEXT = "_zx_permission_context"
STATE_ENTITY = "_zx_entity"
STATE_EVENT_CACHE = "_zx_event_cache"
STATE_PLAIN_TEXT = "_zx_plain_text"
STATE_ROUTE_MODULES = "_zx_route_modules"
STATE_IS_SUPERUSER = "_zx_is_superuser"
STATE_PERMISSION_SIDE_EFFECTS = "_zx_permission_side_effects"
EVENT_CACHE_PERMISSION_SIDE_EFFECTS = "permission_side_effects"

EVENT_CACHE = (
    CacheDict("AUTH_EVENT_CACHE", expire=AUTH_EVENT_CACHE_TTL)
    if AUTH_EVENT_CACHE_TTL > 0
    else None
)

if TYPE_CHECKING:
    from zhenxun.builtin_plugins.hooks.auth_side_effect import SideEffectCommit


@dataclass
class EventContext:
    bot_id: str
    platform: str
    event_type: str
    message_id: str | int | None
    entity: EntityIDs
    plain_text: str = ""
    route_modules: set[str] = field(default_factory=set)
    route_modules_loaded: bool = False
    is_superuser: bool = False
    event_cache: dict[str, Any] | None = None

    @property
    def user_id(self) -> str:
        return self.entity.user_id

    @property
    def group_id(self) -> str | None:
        return self.entity.group_id

    @property
    def channel_id(self) -> str | None:
        return self.entity.channel_id


@dataclass
class PermissionSideEffectCache:
    auth_results: dict[str, tuple[bool, str | None]] = field(default_factory=dict)
    module_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    commits: dict[str, "SideEffectCommit"] = field(default_factory=dict)

    def lock_for(self, module: str) -> asyncio.Lock:
        lock = self.module_locks.get(module)
        if lock is None:
            lock = asyncio.Lock()
            self.module_locks[module] = lock
        return lock


@dataclass
class PermissionContext:
    event: EventContext
    module: str
    plugin: Any = None
    user: Any = None
    group: Any = None
    bot_data: Any = None
    admin_levels: Any = None

    @property
    def entity(self) -> EntityIDs:
        return self.event.entity

    @property
    def user_id(self) -> str:
        return self.event.user_id

    @property
    def group_id(self) -> str | None:
        return self.event.group_id

    @property
    def channel_id(self) -> str | None:
        return self.event.channel_id

    @property
    def plain_text(self) -> str:
        return self.event.plain_text

    @property
    def is_superuser(self) -> bool:
        return self.event.is_superuser


def resolve_actor_user_id(event: Event, fallback_user_id: str | None) -> str:
    """优先使用事件发起者 ID，避免 notice 场景 session.user 指向 bot 自身。"""
    event_user_id = getattr(event, "user_id", None)
    if event_user_id is None:
        return fallback_user_id or ""
    resolved = str(event_user_id)
    return resolved or fallback_user_id or ""


def resolve_event_group_id(event: Event, fallback_group_id: str | None) -> str | None:
    """notice 场景 session.group 可能缺失，回退到事件上的 group_id。"""
    event_group_id = getattr(event, "group_id", None)
    if event_group_id is None:
        return fallback_group_id
    resolved = str(event_group_id)
    return resolved or fallback_group_id


def resolve_event_channel_id(
    event: Event, fallback_channel_id: str | None
) -> str | None:
    """频道场景回退到事件上的 channel_id。"""
    event_channel_id = getattr(event, "channel_id", None)
    if event_channel_id is None:
        return fallback_channel_id
    resolved = str(event_channel_id)
    return resolved or fallback_channel_id


def resolve_entity_ids(event: Event, session: Uninfo) -> EntityIDs:
    entity = get_entity_ids(session)
    entity.user_id = resolve_actor_user_id(event, entity.user_id)
    entity.group_id = resolve_event_group_id(event, entity.group_id)
    entity.channel_id = resolve_event_channel_id(event, entity.channel_id)
    return entity


def extract_plain_text(message: UniMsg | None, event: Event) -> str:
    if message is not None:
        with contextlib.suppress(Exception):
            return message.extract_plain_text()
    with contextlib.suppress(Exception):
        plain = event.get_plaintext()
        if plain:
            return plain.strip()
    return ""


def _event_message_id(event: Event) -> str | int | None:
    msg_id = getattr(event, "message_id", None)
    if msg_id is None:
        msg_id = getattr(event, "id", None)
    return msg_id


def event_cache_key(
    event: Event,
    *,
    bot_id: str,
    platform: str,
    entity: EntityIDs,
) -> str:
    msg_id = _event_message_id(event)
    if msg_id is None:
        msg_id = id(event)
    group_id = entity.group_id or ""
    channel_id = entity.channel_id or ""
    return f"{platform}:{bot_id}:{entity.user_id}:{group_id}:{channel_id}:{msg_id}"


def get_event_cache(
    event: Event,
    *,
    bot_id: str,
    platform: str,
    entity: EntityIDs,
) -> dict[str, Any] | None:
    if not EVENT_CACHE:
        return None
    key = event_cache_key(event, bot_id=bot_id, platform=platform, entity=entity)
    try:
        return EVENT_CACHE[key]
    except KeyError:
        cache: dict[str, Any] = {}
        EVENT_CACHE[key] = cache
        return cache


def _sync_context_state(state: dict[str, Any], context: EventContext) -> None:
    state[STATE_EVENT_CONTEXT] = context
    state[STATE_ENTITY] = context.entity
    state[STATE_EVENT_CACHE] = context.event_cache
    state[STATE_PLAIN_TEXT] = context.plain_text
    state[STATE_ROUTE_MODULES] = context.route_modules
    state[STATE_IS_SUPERUSER] = context.is_superuser
    get_permission_side_effect_cache(state=state, event_cache=context.event_cache)


def get_permission_side_effect_cache(
    *,
    state: dict[str, Any] | None = None,
    event_cache: dict[str, Any] | None = None,
) -> PermissionSideEffectCache:
    side_effects = None
    if state is not None:
        side_effects = state.get(STATE_PERMISSION_SIDE_EFFECTS)
    if (
        not isinstance(side_effects, PermissionSideEffectCache)
        and event_cache is not None
    ):
        side_effects = event_cache.get(EVENT_CACHE_PERMISSION_SIDE_EFFECTS)
    if not isinstance(side_effects, PermissionSideEffectCache):
        side_effects = PermissionSideEffectCache()
    if state is not None:
        state[STATE_PERMISSION_SIDE_EFFECTS] = side_effects
    if event_cache is not None:
        event_cache[EVENT_CACHE_PERMISSION_SIDE_EFFECTS] = side_effects
    return side_effects


def get_event_context(state: dict[str, Any] | None) -> EventContext | None:
    if state is None:
        return None
    context = state.get(STATE_EVENT_CONTEXT)
    return context if isinstance(context, EventContext) else None


def get_or_create_event_context(
    bot: Bot,
    event: Event,
    session: Uninfo,
    state: dict[str, Any],
    *,
    message: UniMsg | None = None,
) -> EventContext:
    context = get_event_context(state)
    if context is not None:
        _sync_context_state(state, context)
        return context

    entity = state.get(STATE_ENTITY)
    if not isinstance(entity, EntityIDs):
        entity = resolve_entity_ids(event, session)

    platform = PlatformUtils.get_platform(session)
    bot_id = str(bot.self_id)
    event_cache = state.get(STATE_EVENT_CACHE)
    if not isinstance(event_cache, dict):
        event_cache = get_event_cache(
            event,
            bot_id=bot_id,
            platform=platform,
            entity=entity,
        )

    text = state.get(STATE_PLAIN_TEXT)
    if not isinstance(text, str):
        cached_text = event_cache.get("plain_text") if event_cache is not None else None
        text = (
            cached_text
            if isinstance(cached_text, str)
            else extract_plain_text(message, event)
        )
    if event_cache is not None:
        event_cache["plain_text"] = text

    route_modules_loaded = STATE_ROUTE_MODULES in state
    route_modules = state.get(STATE_ROUTE_MODULES)
    if not isinstance(route_modules, set):
        cached_routes = (
            event_cache.get("route_modules") if event_cache is not None else None
        )
        route_modules = cached_routes if isinstance(cached_routes, set) else set()
        route_modules_loaded = isinstance(cached_routes, set)

    is_superuser = state.get(STATE_IS_SUPERUSER)
    if not isinstance(is_superuser, bool):
        is_superuser = entity.user_id in bot.config.superusers

    context = EventContext(
        bot_id=bot_id,
        platform=platform,
        event_type=event.get_type(),
        message_id=_event_message_id(event),
        entity=entity,
        plain_text=text,
        route_modules=route_modules,
        route_modules_loaded=route_modules_loaded,
        is_superuser=is_superuser,
        event_cache=event_cache,
    )
    _sync_context_state(state, context)
    return context


def set_route_modules(
    state: dict[str, Any] | None,
    context: EventContext,
    route_modules: set[str],
) -> None:
    context.route_modules = route_modules
    context.route_modules_loaded = True
    if context.event_cache is not None:
        context.event_cache["route_modules"] = route_modules
    if state is not None:
        _sync_context_state(state, context)


def store_permission_context(
    state: dict[str, Any] | None, context: PermissionContext
) -> None:
    if state is not None:
        state[STATE_PERMISSION_CONTEXT] = context

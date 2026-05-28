from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from zhenxun.services.cache.runtime_cache import (
    BanMemoryCache,
    BotMemoryCache,
    BotSnapshot,
    GroupMemoryCache,
    GroupSnapshot,
    LevelUserMemoryCache,
    LevelUserSnapshot,
)

from .auth.context import EventContext
from .auth_profile import PluginAuthProfile

if TYPE_CHECKING:
    from nonebot.adapters import Bot


@dataclass(slots=True)
class AuthSnapshot:
    context: EventContext
    plugin: object
    profile: PluginAuthProfile
    bot_data: BotSnapshot | None = None
    group: GroupSnapshot | None = None
    admin_levels: tuple[LevelUserSnapshot | None, LevelUserSnapshot | None] | None = (
        None
    )
    ban_state: bool | None = None
    user_balance_loaded: bool = False
    user_balance: int | None = None
    cache_misses: frozenset[str] = field(default_factory=frozenset)

    @property
    def module(self) -> str:
        return self.profile.module

    @property
    def is_superuser(self) -> bool:
        return self.context.is_superuser

    @property
    def user_id(self) -> str:
        return self.context.user_id

    @property
    def group_id(self) -> str | None:
        return self.context.group_id

    @property
    def channel_id(self) -> str | None:
        return self.context.channel_id

    @property
    def has_ban_cache(self) -> bool:
        return self.ban_state is not None

    @property
    def cache_ready(self) -> bool:
        return not self.cache_misses


async def build_auth_snapshot(
    *,
    context: EventContext,
    plugin: object,
    profile: PluginAuthProfile,
    bot: "Bot",
    skip_ban: bool = False,
    allow_cache_load: bool = False,
) -> AuthSnapshot:
    event_cache = context.event_cache
    entity = context.entity
    cache_misses: set[str] = set()

    bot_data: BotSnapshot | None = None
    if (
        event_cache is not None
        and "bot_data" in event_cache
        and (event_cache.get("bot_cache_ready") or not allow_cache_load)
    ):
        bot_data = event_cache.get("bot_data")
    else:
        bot_data = BotMemoryCache.get_if_ready(bot.self_id)
        if bot_data is None:
            if allow_cache_load:
                bot_data = await BotMemoryCache.get(bot.self_id)
            elif not BotMemoryCache.is_loaded():
                cache_misses.add("bot")
        if event_cache is not None:
            event_cache["bot_data"] = bot_data
            event_cache["bot_cache_ready"] = BotMemoryCache.is_loaded()

    group = None
    if entity.group_id:
        if (
            event_cache is not None
            and "group" in event_cache
            and (event_cache.get("group_cache_ready") or not allow_cache_load)
        ):
            group = event_cache.get("group")
        else:
            group = GroupMemoryCache.get_if_ready(entity.group_id, entity.channel_id)
            if group is None and not GroupMemoryCache.is_loaded():
                cache_misses.add("group")
            elif group is None and allow_cache_load:
                group = await GroupMemoryCache.get(entity.group_id, entity.channel_id)
            if event_cache is not None:
                event_cache["group"] = group
                event_cache["group_cache_ready"] = GroupMemoryCache.is_loaded()

    admin_levels = None
    if profile.need_admin:
        if (
            event_cache is not None
            and "admin_levels" in event_cache
            and (event_cache.get("admin_cache_ready") or not allow_cache_load)
        ):
            admin_levels = event_cache.get("admin_levels")
        else:
            admin_levels = LevelUserMemoryCache.get_levels_if_ready(
                entity.user_id,
                entity.group_id,
            )
            if admin_levels is None:
                if allow_cache_load:
                    admin_levels = await LevelUserMemoryCache.get_levels(
                        entity.user_id,
                        entity.group_id,
                    )
                else:
                    cache_misses.add("admin_levels")
            if event_cache is not None:
                event_cache["admin_levels"] = admin_levels
                event_cache["admin_cache_ready"] = LevelUserMemoryCache.is_loaded()

    ban_state = None
    if not skip_ban:
        if event_cache is not None and "ban_state" in event_cache:
            ban_state = event_cache.get("ban_state")
        elif BanMemoryCache.is_loaded():
            ban_state = BanMemoryCache.is_banned(entity.user_id, entity.group_id)
            if event_cache is not None:
                event_cache["ban_state"] = ban_state
        elif allow_cache_load:
            await BanMemoryCache.ensure_loaded()
            ban_state = BanMemoryCache.is_banned(entity.user_id, entity.group_id)
            if event_cache is not None:
                event_cache["ban_state"] = ban_state
        else:
            cache_misses.add("ban")

    return AuthSnapshot(
        context=context,
        plugin=plugin,
        profile=profile,
        bot_data=bot_data,
        group=group,
        admin_levels=admin_levels,
        ban_state=ban_state,
        cache_misses=frozenset(cache_misses),
    )


async def get_or_build_auth_snapshot(
    *,
    context: EventContext,
    plugin: object,
    profile: PluginAuthProfile,
    bot: "Bot",
    skip_ban: bool = False,
    allow_cache_load: bool = False,
) -> AuthSnapshot:
    event_cache = context.event_cache
    module = profile.module
    if event_cache is not None:
        snapshot_cache = event_cache.setdefault("auth_snapshots", {})
        cached = snapshot_cache.get(module)
        if isinstance(cached, AuthSnapshot):
            if not (allow_cache_load and cached.cache_misses):
                return cached
    snapshot = await build_auth_snapshot(
        context=context,
        plugin=plugin,
        profile=profile,
        bot=bot,
        skip_ban=skip_ban,
        allow_cache_load=allow_cache_load,
    )
    if event_cache is not None:
        event_cache.setdefault("auth_snapshots", {})[module] = snapshot
    return snapshot


__all__ = ["AuthSnapshot", "build_auth_snapshot", "get_or_build_auth_snapshot"]

from __future__ import annotations

from dataclasses import dataclass
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


async def build_auth_snapshot(
    *,
    context: EventContext,
    plugin: object,
    profile: PluginAuthProfile,
    bot: "Bot",
    skip_ban: bool = False,
) -> AuthSnapshot:
    event_cache = context.event_cache
    entity = context.entity

    bot_data: BotSnapshot | None = None
    if event_cache is not None and "bot_data" in event_cache:
        bot_data = event_cache.get("bot_data")
    else:
        bot_data = await BotMemoryCache.get(bot.self_id)
        if event_cache is not None:
            event_cache["bot_data"] = bot_data
            event_cache["bot_timeout"] = False

    group = None
    if entity.group_id:
        if event_cache is not None and "group" in event_cache:
            group = event_cache.get("group")
        else:
            group = GroupMemoryCache.get_if_ready(entity.group_id, entity.channel_id)
            if event_cache is not None:
                event_cache["group"] = group

    admin_levels = None
    if profile.need_admin:
        if event_cache is not None and "admin_levels" in event_cache:
            admin_levels = event_cache.get("admin_levels")
        else:
            admin_levels = await LevelUserMemoryCache.get_levels(
                entity.user_id,
                entity.group_id,
            )
            if event_cache is not None:
                event_cache["admin_levels"] = admin_levels
                event_cache["admin_timeout"] = False

    ban_state = None
    if not skip_ban:
        if event_cache is not None and "ban_state" in event_cache:
            ban_state = event_cache.get("ban_state")
        elif BanMemoryCache.is_loaded():
            ban_state = BanMemoryCache.is_banned(entity.user_id, entity.group_id)
            if event_cache is not None:
                event_cache["ban_state"] = ban_state

    return AuthSnapshot(
        context=context,
        plugin=plugin,
        profile=profile,
        bot_data=bot_data,
        group=group,
        admin_levels=admin_levels,
        ban_state=ban_state,
    )


__all__ = ["AuthSnapshot", "build_auth_snapshot"]

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING

from zhenxun.services.cache.runtime_cache import (
    BotSnapshot,
    GroupSnapshot,
    LevelUserSnapshot,
)
from zhenxun.services.db_context import with_db_timeout
from zhenxun.services.log import logger
from zhenxun.services.message_load import is_db_unhealthy

from .auth.config import LOGGER_COMMAND
from .auth.context import EventContext
from .auth.data_provider import (
    DEFAULT_PERMISSION_DATA_PROVIDER,
    PermissionDataProvider,
)
from .auth_profile import PluginAuthProfile

if TYPE_CHECKING:
    from nonebot.adapters import Bot

QQ_CLIENT_GROUP_REPAIR_TTL = 60
_QQ_CLIENT_GROUP_REPAIR_FAILURES: dict[tuple[str, str], float] = {}
_QQ_CLIENT_GROUP_REPAIR_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def _build_runtime_group_snapshot(context: EventContext) -> GroupSnapshot | None:
    """Provide a non-persistent default group for QQ official runtime auth."""
    if context.platform_scope != "qq_api" or not context.group_id:
        return None
    return GroupSnapshot(
        group_id=context.group_id,
        channel_id=context.channel_id,
        group_name="",
        max_member_count=0,
        member_count=0,
        status=True,
        level=5,
        is_super=False,
        group_flag=0,
        block_plugin="",
        superuser_block_plugin="",
        block_task="",
        superuser_block_task="",
        platform=context.platform,
    )


def _build_default_bot_snapshot(context: EventContext) -> BotSnapshot:
    """Fail-open bot snapshot used only while DB cold-path is unhealthy."""
    return BotSnapshot(
        bot_id=context.bot_id,
        status=True,
        platform=context.platform,
        block_plugins="",
        block_tasks="",
        available_plugins="",
        available_tasks="",
    )


def _build_default_group_snapshot(context: EventContext) -> GroupSnapshot | None:
    """Fail-open group snapshot used only while DB cold-path is unhealthy."""
    if not context.group_id:
        return None
    return GroupSnapshot(
        group_id=context.group_id,
        channel_id=context.channel_id,
        group_name="",
        max_member_count=0,
        member_count=0,
        status=True,
        level=5,
        is_super=False,
        group_flag=0,
        block_plugin="",
        superuser_block_plugin="",
        block_task="",
        superuser_block_task="",
        platform=context.platform,
    )


def _qq_client_group_repair_key(context: EventContext) -> tuple[str, str] | None:
    if context.platform_scope != "qq_client" or not context.group_id:
        return None
    return (context.group_id, context.channel_id or "")


def _qq_client_group_repair_on_cooldown(key: tuple[str, str]) -> bool:
    expire_at = _QQ_CLIENT_GROUP_REPAIR_FAILURES.get(key)
    if not expire_at:
        return False
    if expire_at <= time.time():
        _QQ_CLIENT_GROUP_REPAIR_FAILURES.pop(key, None)
        return False
    return True


async def _repair_missing_qq_client_group(
    context: EventContext,
    *,
    provider: PermissionDataProvider,
) -> GroupSnapshot | None:
    """Persist a minimal OneBot group when startup group sync returned empty."""
    if is_db_unhealthy():
        return None
    key = _qq_client_group_repair_key(context)
    if key is None or not provider.group_cache_loaded():
        return None
    group_id, _ = key
    if _qq_client_group_repair_on_cooldown(key):
        return None

    try:
        from zhenxun.models.group_console import GroupConsole

        lock = _QQ_CLIENT_GROUP_REPAIR_LOCKS.setdefault(key, asyncio.Lock())
        async with lock:
            existing = provider.get_group_if_ready(
                group_id,
                context.channel_id,
            )
            if existing is not None:
                return existing
            defaults = {
                "group_name": "",
                "max_member_count": 0,
                "member_count": 0,
                "group_flag": 1,
                "platform": context.platform,
            }
            group, _ = await with_db_timeout(
                GroupConsole.get_or_create_root_group(
                    group_id=group_id,
                    defaults=defaults,
                ),
                timeout=2.0,
                operation="GroupConsole.get_or_create_root_group",
                source="auth_snapshot.repair_missing_group",
            )
        from zhenxun.services.cache.runtime_cache import GroupMemoryCache

        await GroupMemoryCache.upsert_from_model(group)
        return GroupSnapshot.from_model(group)
    except Exception as exc:
        _QQ_CLIENT_GROUP_REPAIR_FAILURES[key] = time.time() + QQ_CLIENT_GROUP_REPAIR_TTL
        logger.warning(
            "协议端群记录缺失自愈失败，已短期跳过重复修复",
            LOGGER_COMMAND,
            group_id=context.group_id,
            e=exc,
        )
        return None


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
    db_unhealthy: bool = False
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
    provider: PermissionDataProvider = DEFAULT_PERMISSION_DATA_PROVIDER,
) -> AuthSnapshot:
    event_cache = context.event_cache
    entity = context.entity
    cache_misses: set[str] = set()
    db_unhealthy = is_db_unhealthy()
    can_load_cache = allow_cache_load and not db_unhealthy

    bot_data: BotSnapshot | None = None
    if (
        event_cache is not None
        and "bot_data" in event_cache
        and (event_cache.get("bot_cache_ready") or not can_load_cache)
    ):
        bot_data = event_cache.get("bot_data")
    else:
        bot_data = provider.get_bot_if_ready(bot.self_id)
        if bot_data is None:
            if can_load_cache:
                bot_data = await provider.get_bot(bot.self_id)
            elif db_unhealthy:
                bot_data = _build_default_bot_snapshot(context)
            elif not provider.bot_cache_loaded():
                cache_misses.add("bot")
        if event_cache is not None:
            event_cache["bot_data"] = bot_data
            event_cache["bot_cache_ready"] = provider.bot_cache_loaded() or db_unhealthy
    if bot_data is None and db_unhealthy:
        bot_data = _build_default_bot_snapshot(context)
        if event_cache is not None:
            event_cache["bot_data"] = bot_data
            event_cache["bot_cache_ready"] = True

    group = None
    if entity.group_id:
        if (
            event_cache is not None
            and "group" in event_cache
            and (event_cache.get("group_cache_ready") or not can_load_cache)
        ):
            group = event_cache.get("group")
        else:
            group = provider.get_group_if_ready(entity.group_id, entity.channel_id)
            if group is None and not provider.group_cache_loaded():
                cache_misses.add("group")
            elif group is None and can_load_cache:
                group = await provider.get_group(entity.group_id, entity.channel_id)
            if group is None and db_unhealthy:
                group = _build_default_group_snapshot(context)
                cache_misses.discard("group")
            if event_cache is not None:
                event_cache["group"] = group
                event_cache["group_cache_ready"] = (
                    provider.group_cache_loaded() or db_unhealthy
                )
        if group is None and db_unhealthy:
            group = _build_default_group_snapshot(context)
            cache_misses.discard("group")
            if event_cache is not None:
                event_cache["group"] = group
                event_cache["group_cache_ready"] = True
        if group is None and not db_unhealthy:
            group = await _repair_missing_qq_client_group(
                context,
                provider=provider,
            )
        if group is None and (runtime_group := _build_runtime_group_snapshot(context)):
            group = runtime_group
            cache_misses.discard("group")
            if event_cache is not None:
                event_cache["group"] = group
                event_cache["group_cache_ready"] = True
                event_cache["group_runtime_virtual"] = True
        elif group is not None:
            cache_misses.discard("group")
            if event_cache is not None:
                event_cache["group"] = group
                event_cache["group_cache_ready"] = True

    admin_levels = None
    if profile.need_admin:
        if (
            event_cache is not None
            and "admin_levels" in event_cache
            and (event_cache.get("admin_cache_ready") or not can_load_cache)
        ):
            admin_levels = event_cache.get("admin_levels")
        else:
            admin_levels = provider.get_admin_levels_if_ready(
                entity.user_id,
                entity.group_id,
            )
            if admin_levels is None:
                if can_load_cache:
                    admin_levels = await provider.get_admin_levels(
                        entity.user_id,
                        entity.group_id,
                    )
                elif db_unhealthy:
                    admin_levels = (None, None)
                else:
                    cache_misses.add("admin_levels")
            if event_cache is not None:
                event_cache["admin_levels"] = admin_levels
                event_cache["admin_cache_ready"] = (
                    provider.admin_cache_loaded() or db_unhealthy
                )
        if admin_levels is None and db_unhealthy and not provider.admin_cache_loaded():
            admin_levels = (None, None)
            cache_misses.discard("admin_levels")
            if event_cache is not None:
                event_cache["admin_levels"] = admin_levels
                event_cache["admin_cache_ready"] = True

    ban_state = None
    if not skip_ban:
        if event_cache is not None and "ban_state" in event_cache:
            ban_state = event_cache.get("ban_state")
        elif provider.ban_cache_loaded():
            ban_state = provider.is_banned(entity.user_id, entity.group_id)
            if event_cache is not None:
                event_cache["ban_state"] = ban_state
        elif can_load_cache:
            await provider.ensure_ban_loaded()
            ban_state = provider.is_banned(entity.user_id, entity.group_id)
            if event_cache is not None:
                event_cache["ban_state"] = ban_state
        elif db_unhealthy:
            ban_state = False
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
        db_unhealthy=db_unhealthy,
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
    provider: PermissionDataProvider = DEFAULT_PERMISSION_DATA_PROVIDER,
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
        provider=provider,
    )
    if event_cache is not None:
        event_cache.setdefault("auth_snapshots", {})[module] = snapshot
    return snapshot


__all__ = ["AuthSnapshot", "build_auth_snapshot", "get_or_build_auth_snapshot"]

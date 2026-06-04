from __future__ import annotations

from typing import TYPE_CHECKING

from zhenxun.services.cache.runtime_cache import (
    BanMemoryCache,
    BotMemoryCache,
    BotSnapshot,
    GroupMemoryCache,
    GroupSnapshot,
    LevelUserMemoryCache,
    LevelUserSnapshot,
    PluginLimitMemoryCache,
    PluginLimitSnapshot,
)

if TYPE_CHECKING:
    from zhenxun.models.plugin_info import PluginInfo


AdminLevels = tuple[LevelUserSnapshot | None, LevelUserSnapshot | None]


class PermissionDataProvider:
    """Auth data facade over runtime caches.

    Permission checks should read stable runtime snapshots through this provider
    instead of reaching into individual cache classes from multiple auth modules.
    The provider does not own policy semantics and does not query the database
    directly.
    """

    @staticmethod
    def plugin_cache_loaded() -> bool:
        from zhenxun.services.cache.runtime_cache import PluginInfoMemoryCache

        return PluginInfoMemoryCache.is_loaded()

    @staticmethod
    def get_plugin_if_ready(module: str) -> "PluginInfo | None":
        from zhenxun.services.cache.runtime_cache import PluginInfoMemoryCache

        return PluginInfoMemoryCache.get_by_module_if_ready(module)

    @staticmethod
    async def get_plugin(module: str) -> "PluginInfo | None":
        from zhenxun.services.cache.runtime_cache import PluginInfoMemoryCache

        return await PluginInfoMemoryCache.get_by_module(module)

    @staticmethod
    def module_limit_cache_loaded() -> bool:
        return PluginLimitMemoryCache.is_loaded()

    @staticmethod
    async def ensure_module_limits_loaded() -> None:
        await PluginLimitMemoryCache.ensure_loaded()

    @staticmethod
    def get_module_limits_if_ready(
        module: str,
    ) -> list[PluginLimitSnapshot] | None:
        return PluginLimitMemoryCache.get_limits_if_ready(module)

    @staticmethod
    async def get_module_limits(module: str) -> list[PluginLimitSnapshot]:
        return await PluginLimitMemoryCache.get_limits(module)

    @staticmethod
    async def get_all_module_limits() -> list[PluginLimitSnapshot]:
        if not PluginLimitMemoryCache.is_loaded():
            await PluginLimitMemoryCache.ensure_loaded()
        return PluginLimitMemoryCache.get_all_limits()

    @staticmethod
    def bot_cache_loaded() -> bool:
        return BotMemoryCache.is_loaded()

    @staticmethod
    def get_bot_if_ready(bot_id: str | None) -> BotSnapshot | None:
        return BotMemoryCache.get_if_ready(bot_id)

    @staticmethod
    async def get_bot(bot_id: str | None) -> BotSnapshot | None:
        return await BotMemoryCache.get(bot_id)

    @staticmethod
    def group_cache_loaded() -> bool:
        return GroupMemoryCache.is_loaded()

    @staticmethod
    def get_group_if_ready(
        group_id: str | None,
        channel_id: str | None = None,
    ) -> GroupSnapshot | None:
        return GroupMemoryCache.get_if_ready(group_id, channel_id)

    @staticmethod
    async def get_group(
        group_id: str | None,
        channel_id: str | None = None,
    ) -> GroupSnapshot | None:
        return await GroupMemoryCache.get(group_id, channel_id)

    @staticmethod
    def admin_cache_loaded() -> bool:
        return LevelUserMemoryCache.is_loaded()

    @staticmethod
    def get_admin_levels_if_ready(
        user_id: str | None,
        group_id: str | None,
    ) -> AdminLevels | None:
        return LevelUserMemoryCache.get_levels_if_ready(user_id, group_id)

    @staticmethod
    async def get_admin_levels(
        user_id: str | None,
        group_id: str | None,
    ) -> AdminLevels:
        return await LevelUserMemoryCache.get_levels(user_id, group_id)

    @staticmethod
    def ban_cache_loaded() -> bool:
        return BanMemoryCache.is_loaded()

    @staticmethod
    async def ensure_ban_loaded() -> None:
        await BanMemoryCache.ensure_loaded()

    @staticmethod
    def is_banned(user_id: str | None, group_id: str | None) -> bool:
        return BanMemoryCache.is_banned(user_id, group_id)

    @staticmethod
    def get_ban_remaining_time(user_id: str | None, group_id: str | None) -> int:
        return BanMemoryCache.remaining_time(user_id, group_id)


DEFAULT_PERMISSION_DATA_PROVIDER = PermissionDataProvider()


__all__ = [
    "DEFAULT_PERMISSION_DATA_PROVIDER",
    "AdminLevels",
    "BotSnapshot",
    "GroupSnapshot",
    "LevelUserSnapshot",
    "PermissionDataProvider",
    "PluginLimitSnapshot",
]

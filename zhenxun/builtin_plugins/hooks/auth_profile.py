from __future__ import annotations

from dataclasses import dataclass

from zhenxun.services.cache.runtime_cache import (
    PluginLimitMemoryCache,
    PluginLimitSnapshot,
)
from zhenxun.utils.enum import BlockType, PluginType


@dataclass(frozen=True, slots=True)
class PluginAuthProfile:
    module: str
    name: str
    hidden: bool = False
    status: bool = True
    block_type: BlockType | None = None
    plugin_type: PluginType | None = None
    need_admin: bool = False
    need_group_check: bool = False
    has_limit: bool = False
    cost_gold: int = 0
    admin_level: int = 0
    limit_superuser: bool = False
    level: int = 0

    @property
    def superuser_only(self) -> bool:
        return self.plugin_type == PluginType.SUPERUSER

    @property
    def superuser_or_admin(self) -> bool:
        return self.plugin_type == PluginType.SUPER_AND_ADMIN


def _plugin_admin_level(plugin) -> int:
    try:
        return int(getattr(plugin, "admin_level", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _plugin_cost_gold(plugin) -> int:
    try:
        return int(getattr(plugin, "cost_gold", 0) or 0)
    except (TypeError, ValueError):
        return 0


def build_plugin_auth_profile(plugin, *, has_limit: bool = False) -> PluginAuthProfile:
    plugin_type = getattr(plugin, "plugin_type", None)
    admin_level = _plugin_admin_level(plugin)
    block_type = getattr(plugin, "block_type", None)
    module = str(getattr(plugin, "module", "") or "")
    need_admin = bool(admin_level > 0) or plugin_type in {
        PluginType.ADMIN,
        PluginType.SUPERUSER,
        PluginType.SUPER_AND_ADMIN,
    }
    return PluginAuthProfile(
        module=module,
        name=str(getattr(plugin, "name", "") or module),
        hidden=plugin_type == PluginType.HIDDEN,
        status=bool(getattr(plugin, "status", True)),
        block_type=block_type,
        plugin_type=plugin_type,
        need_admin=need_admin,
        need_group_check=block_type
        in {BlockType.ALL, BlockType.GROUP, BlockType.PRIVATE},
        has_limit=bool(has_limit),
        cost_gold=_plugin_cost_gold(plugin),
        admin_level=admin_level,
        limit_superuser=bool(getattr(plugin, "limit_superuser", False)),
        level=int(getattr(plugin, "level", 0) or 0),
    )


async def get_plugin_auth_profile(
    plugin,
    *,
    event_cache: dict | None = None,
    allow_cache_load: bool = True,
) -> PluginAuthProfile:
    module = str(getattr(plugin, "module", "") or "")
    profile_cache: dict[str, PluginAuthProfile] = {}
    if event_cache is not None:
        profile_cache = event_cache.setdefault("plugin_auth_profiles", {})
        cached = profile_cache.get(module)
        if cached is not None:
            return cached

    limits: list[PluginLimitSnapshot] | None = None
    limits_ready = False
    if event_cache is not None:
        limit_cache = event_cache.setdefault("module_limit_entries", {})
        if module in limit_cache:
            limits = limit_cache[module]
            limits_ready = True
    if limits is None:
        limits = PluginLimitMemoryCache.get_limits_if_ready(module)
        limits_ready = limits is not None
    if limits is None and allow_cache_load:
        limits = await PluginLimitMemoryCache.get_limits(module)
        limits_ready = True
    if limits is None:
        limits = []
    profile = build_plugin_auth_profile(plugin, has_limit=bool(limits))
    if event_cache is not None:
        profile_cache[module] = profile
        event_cache.setdefault("module_limits", {})[module] = profile.has_limit
        event_cache.setdefault("module_limits_ready", {})[module] = limits_ready
        if limits_ready:
            event_cache.setdefault("module_limit_entries", {})[module] = limits
    return profile


__all__ = [
    "PluginAuthProfile",
    "build_plugin_auth_profile",
    "get_plugin_auth_profile",
]

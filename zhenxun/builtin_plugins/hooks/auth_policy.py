from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Literal

from zhenxun.services.cache.runtime_cache import _parse_block_modules
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.enum import BlockType, PluginType

from .auth.exception import IsSuperuserException, SkipPluginException
from .auth_profile import PluginAuthProfile
from .auth_snapshot import AuthSnapshot

PolicyEffect = Literal["allow", "deny", "skip", "defer"]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    effect: PolicyEffect
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.effect == "allow"

    @property
    def denied(self) -> bool:
        return self.effect == "deny"

    @property
    def skipped(self) -> bool:
        return self.effect == "skip"

    @property
    def deferred(self) -> bool:
        return self.effect == "defer"


@dataclass(frozen=True, slots=True)
class PolicyPrincipal:
    user_id: str
    group_id: str | None = None
    channel_id: str | None = None
    is_superuser: bool = False


@dataclass(frozen=True, slots=True)
class PolicyAction:
    name: str
    module: str


@dataclass(frozen=True, slots=True)
class PolicyResource:
    plugin: object
    profile: PluginAuthProfile


@dataclass(frozen=True, slots=True)
class PolicyContext:
    snapshot: AuthSnapshot
    allow_sleep_bypass: bool = False
    allow_group_sleep_bypass: bool = False


class PolicyDecisionPoint:
    """Structured permission decision helpers.

    This layer mirrors existing auth semantics and deliberately does not add a
    new policy table. Side-effecting checks such as limit counters remain
    deferred to the old hooks.
    """

    @staticmethod
    def _missing(snapshot: AuthSnapshot, name: str) -> bool:
        return name in snapshot.cache_misses

    @staticmethod
    def _private_disabled(profile: PluginAuthProfile) -> bool:
        return profile.block_type == BlockType.PRIVATE

    @staticmethod
    def _group_disabled(profile: PluginAuthProfile) -> bool:
        return profile.block_type == BlockType.GROUP

    @staticmethod
    def _globally_disabled(profile: PluginAuthProfile) -> bool:
        return profile.block_type == BlockType.ALL and not profile.status

    def decide(
        self,
        principal: PolicyPrincipal,
        action: PolicyAction,
        resource: PolicyResource,
        context: PolicyContext,
    ) -> PolicyDecision:
        del action
        snapshot = context.snapshot
        profile = resource.profile
        if profile.hidden:
            return PolicyDecision("allow", "hidden_plugin_skip_auth")
        if snapshot.ban_state is True and not principal.is_superuser:
            return PolicyDecision("deny", "user_or_group_banned")
        if profile.superuser_only and not principal.is_superuser:
            return PolicyDecision("deny", "superuser_required")
        return PolicyDecision("defer", "needs_legacy_hooks")

    def decide_bot(self, context: PolicyContext) -> PolicyDecision:
        snapshot = context.snapshot
        bot_data = snapshot.bot_data
        if bot_data is None:
            if self._missing(snapshot, "bot"):
                return PolicyDecision("defer", "bot_cache_unavailable")
            return PolicyDecision("deny", "bot_not_found")
        if not bot_data.status and not context.allow_sleep_bypass:
            return PolicyDecision("deny", "bot_sleeping")
        module = snapshot.profile.module
        if module:
            value = bot_data.block_plugins or ""
            # 缓存解析后的 frozenset,避免每次 bot 检查重复 split(B8-3);
            # 仍保留原子串判定以保持行为等价。
            if CommonUtils.format(module) in value or module in self._bot_block_set(
                bot_data
            ):
                return PolicyDecision("deny", "bot_plugin_blocked")
        return PolicyDecision("allow", "bot_allowed")

    def decide_group(self, context: PolicyContext) -> PolicyDecision:
        snapshot = context.snapshot
        if not snapshot.group_id:
            return PolicyDecision("skip", "not_group_event")
        group = snapshot.group
        profile = snapshot.profile
        if group is None:
            if self._missing(snapshot, "group"):
                return PolicyDecision("defer", "group_cache_unavailable")
            return PolicyDecision("deny", "group_not_found")
        if group.level < 0:
            return PolicyDecision("deny", "group_blacklisted")
        if (
            not group.status
            and not context.allow_group_sleep_bypass
            and not snapshot.is_superuser
        ):
            return PolicyDecision("deny", "group_sleeping")
        if profile.level > group.level:
            return PolicyDecision("deny", "group_level_low")
        return PolicyDecision("allow", "group_allowed")

    def decide_admin(self, context: PolicyContext) -> PolicyDecision:
        snapshot = context.snapshot
        profile = snapshot.profile
        if not profile.need_admin:
            return PolicyDecision("skip", "admin_not_required")
        if profile.plugin_type in {PluginType.SUPERUSER, PluginType.SUPER_AND_ADMIN}:
            if snapshot.is_superuser:
                return PolicyDecision("allow", "superuser")
            if profile.plugin_type == PluginType.SUPERUSER:
                return PolicyDecision("deny", "superuser_required")
        if not profile.admin_level:
            return PolicyDecision("skip", "admin_level_empty")
        if snapshot.admin_levels is None:
            return PolicyDecision("defer", "admin_levels_unavailable")
        global_user, group_user = snapshot.admin_levels
        user_level = global_user.user_level if global_user else 0
        if snapshot.group_id and group_user:
            user_level = max(user_level, group_user.user_level)
        if user_level < profile.admin_level:
            return PolicyDecision("deny", "admin_level_low")
        return PolicyDecision("allow", "admin_allowed")

    def decide_plugin(self, context: PolicyContext) -> PolicyDecision:
        snapshot = context.snapshot
        profile = snapshot.profile
        group = snapshot.group
        if snapshot.is_superuser:
            return PolicyDecision("allow", "superuser")
        if snapshot.group_id:
            if group is None:
                if self._missing(snapshot, "group"):
                    return PolicyDecision("defer", "group_cache_unavailable")
                return PolicyDecision("deny", "group_not_found")
            if profile.status and not self._group_disabled(profile):
                block_set, super_block_set = self._group_block_sets(group)
                if not block_set and not super_block_set:
                    return PolicyDecision("allow", "plugin_group_fast_allow")
            block_set, super_block_set = self._group_block_sets(group)
            if profile.module in super_block_set:
                return PolicyDecision("deny", "plugin_superuser_blocked_in_group")
            if profile.module in block_set:
                return PolicyDecision("deny", "plugin_blocked_in_group")
            if self._group_disabled(profile):
                return PolicyDecision("deny", "plugin_disabled_in_group")
        elif self._private_disabled(profile):
            return PolicyDecision("deny", "plugin_disabled_in_private")
        if self._globally_disabled(profile):
            if group is not None and getattr(group, "is_super", False):
                return PolicyDecision("allow", "super_group_bypass")
            return PolicyDecision("deny", "plugin_global_disabled")
        return PolicyDecision("allow", "plugin_allowed")

    @staticmethod
    def _group_block_sets(group: object) -> tuple[frozenset[str], frozenset[str]]:
        block_set = getattr(group, "block_plugin_set", None)
        super_block_set = getattr(group, "superuser_block_plugin_set", None)
        if block_set is None:
            block_set = _parse_block_modules(getattr(group, "block_plugin", "") or "")
            setattr(group, "block_plugin_set", block_set)
        if super_block_set is None:
            super_block_set = _parse_block_modules(
                getattr(group, "superuser_block_plugin", "") or ""
            )
            setattr(group, "superuser_block_plugin_set", super_block_set)
        return block_set, super_block_set

    @staticmethod
    def _bot_block_set(bot_data: object) -> frozenset[str]:
        block_set = getattr(bot_data, "block_plugin_set", None)
        if block_set is None:
            block_set = _parse_block_modules(
                getattr(bot_data, "block_plugins", "") or ""
            )
            with contextlib.suppress(Exception):
                setattr(bot_data, "block_plugin_set", block_set)
        return block_set

    @staticmethod
    def _module_in_block_string(module: str, value: str | None) -> bool:
        if not value:
            return False
        return CommonUtils.format(module) in value or module in _parse_block_modules(
            value
        )


def principal_from_snapshot(snapshot: AuthSnapshot) -> PolicyPrincipal:
    return PolicyPrincipal(
        user_id=snapshot.user_id,
        group_id=snapshot.group_id,
        channel_id=snapshot.channel_id,
        is_superuser=snapshot.is_superuser,
    )


def action_from_snapshot(snapshot: AuthSnapshot) -> PolicyAction:
    return PolicyAction(name="invoke_plugin", module=snapshot.module)


def resource_from_snapshot(snapshot: AuthSnapshot) -> PolicyResource:
    return PolicyResource(plugin=snapshot.plugin, profile=snapshot.profile)


def raise_for_policy(decision: PolicyDecision, message: str | None = None) -> None:
    if decision.denied:
        raise SkipPluginException(message or decision.reason)
    if decision.allowed and decision.reason == "super_group_bypass":
        raise IsSuperuserException()


__all__ = [
    "PolicyAction",
    "PolicyContext",
    "PolicyDecision",
    "PolicyDecisionPoint",
    "PolicyPrincipal",
    "PolicyResource",
    "action_from_snapshot",
    "principal_from_snapshot",
    "raise_for_policy",
    "resource_from_snapshot",
]

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from zhenxun.utils.enum import BlockType, PluginType

from .auth.exception import SkipPluginException
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
    route_skip_checks: bool = False
    allow_sleep_bypass: bool = False


class PolicyDecisionPoint:
    """Structured permission decision helpers.

    This layer mirrors existing auth semantics and deliberately does not add a
    new policy table. Side-effecting checks such as limit counters remain
    deferred to the old hooks.
    """

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
        if context.route_skip_checks:
            return PolicyDecision("allow", "route_miss_skip_checks")
        if snapshot.ban_state is True and not principal.is_superuser:
            return PolicyDecision("deny", "user_or_group_banned")
        if profile.superuser_only and not principal.is_superuser:
            return PolicyDecision("deny", "superuser_required")
        return PolicyDecision("defer", "needs_legacy_hooks")

    def decide_bot(self, context: PolicyContext) -> PolicyDecision:
        snapshot = context.snapshot
        bot_data = snapshot.bot_data
        if bot_data is None:
            return PolicyDecision("deny", "bot_not_found")
        if not bot_data.status and not context.allow_sleep_bypass:
            return PolicyDecision("deny", "bot_sleeping")
        module = snapshot.profile.module
        if module and f"<{module}," in (bot_data.block_plugins or ""):
            return PolicyDecision("deny", "bot_plugin_blocked")
        return PolicyDecision("allow", "bot_allowed")

    def decide_group(self, context: PolicyContext) -> PolicyDecision:
        snapshot = context.snapshot
        if not snapshot.group_id:
            return PolicyDecision("skip", "not_group_event")
        group = snapshot.group
        profile = snapshot.profile
        if group is None:
            return PolicyDecision("deny", "group_not_found")
        if group.level < 0:
            return PolicyDecision("deny", "group_blacklisted")
        if not group.status:
            return PolicyDecision("defer", "group_sleep_check_needs_wake_rule")
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
                return PolicyDecision("deny", "group_not_found")
            if profile.block_type == BlockType.GROUP:
                return PolicyDecision("deny", "plugin_disabled_in_group")
            if profile.module in getattr(group, "superuser_block_plugin_set", ()):
                return PolicyDecision("deny", "plugin_superuser_blocked_in_group")
            if profile.module in getattr(group, "block_plugin_set", ()):
                return PolicyDecision("deny", "plugin_blocked_in_group")
        elif profile.block_type == BlockType.PRIVATE:
            return PolicyDecision("deny", "plugin_disabled_in_private")
        if profile.block_type == BlockType.ALL and not profile.status:
            if group is not None and getattr(group, "is_super", False):
                return PolicyDecision("allow", "super_group_bypass")
            return PolicyDecision("deny", "plugin_global_disabled")
        return PolicyDecision("allow", "plugin_allowed")


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

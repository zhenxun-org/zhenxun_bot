from __future__ import annotations

from dataclasses import dataclass, field

from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.user_console import UserConsole

from .auth.context import PermissionContext
from .auth_policy import PolicyContext
from .auth_profile import PluginAuthProfile
from .auth_snapshot import AuthSnapshot


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
    should_return_allowed: bool = False


@dataclass(slots=True)
class AuthLaneContext:
    lane: str = "passive_light"
    scope_key: str = ""
    queue_size: int = 0

    @property
    def is_guaranteed(self) -> bool:
        return self.lane.startswith("command_") or self.lane == "system"


@dataclass(slots=True)
class EventDispatchContext:
    event_type: str
    plain_text: str = ""
    raw_text: str = ""
    trie_command_text: str = ""
    trie_raw_command: str = ""
    text_candidates: tuple[str, ...] = ()
    to_me: bool = False
    has_url: bool = False
    has_image: bool = False
    is_command_like: bool = False
    route_modules: set[str] = field(default_factory=set)
    ai_route_modules: set[str] = field(default_factory=set)
    ai_route_heads: set[str] = field(default_factory=set)


__all__ = [
    "AuthLaneContext",
    "AuthPolicyFlags",
    "AuthPreparation",
    "EventDispatchContext",
]

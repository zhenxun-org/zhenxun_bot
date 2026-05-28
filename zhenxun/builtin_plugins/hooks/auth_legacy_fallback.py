from __future__ import annotations

from nonebot.adapters import Event
from nonebot_plugin_uninfo import Uninfo

from .auth.auth_admin import auth_admin
from .auth.auth_bot import auth_bot
from .auth.auth_group import auth_group
from .auth.auth_plugin import auth_plugin
from .auth_types import AuthPreparation


async def legacy_pure_auth_fallback(
    *,
    prep: AuthPreparation,
    event: Event,
    session: Uninfo,
    text: str,
) -> None:
    """Compatibility fallback for cache-deferred pure permission checks."""

    await auth_bot(
        prep.plugin,
        prep.snapshot.context.bot_id,
        prep.snapshot.bot_data,
        skip_fetch=prep.snapshot.bot_data is not None,
        allow_sleep_bypass=prep.policy_context.allow_sleep_bypass,
        context=prep.permission_context,
    )
    await auth_group(
        prep.plugin,
        prep.snapshot.group,
        text,
        prep.snapshot.group_id,
        context=prep.permission_context,
    )
    await auth_plugin(
        prep.plugin,
        prep.snapshot.group,
        session,
        event,
        context=prep.permission_context,
        user_id=prep.snapshot.user_id,
    )
    await auth_admin(
        prep.plugin,
        session,
        cached_levels=prep.snapshot.admin_levels,
        context=prep.permission_context,
        entity=prep.snapshot.context.entity,
    )


__all__ = ["legacy_pure_auth_fallback"]

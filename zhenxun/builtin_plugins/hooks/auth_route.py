from __future__ import annotations

from collections.abc import Awaitable, Callable

from nonebot.matcher import Matcher

from zhenxun.utils.enum import PluginType

from .auth.context import EventContext, set_route_modules

RouteContextGetter = Callable[[str, dict | None], Awaitable[set[str]]]
CommandMatcherChecker = Callable[[type[Matcher]], bool]
AlconnaShortcutChecker = Callable[[type[Matcher]], bool]


async def route_precheck(
    matcher: Matcher,
    context: EventContext,
    *,
    route_modules_with_commands: set[str],
    get_route_context: RouteContextGetter,
    is_command_matcher_class: CommandMatcherChecker,
    matcher_has_alconna_shortcuts: AlconnaShortcutChecker,
) -> bool:
    """Skip expensive auth checks for command matchers proven to be off-route."""

    module = matcher.plugin_name or ""
    if not module:
        return False
    if _is_hidden_plugin(matcher):
        return False
    if not is_command_matcher_class(type(matcher)):
        return False

    route_modules = context.route_modules if context.route_modules_loaded else None
    if route_modules is None:
        route_modules = await get_route_context(
            context.plain_text,
            context.event_cache,
        )
        set_route_modules(None, context, route_modules)

    if module in route_modules_with_commands and module not in route_modules:
        if matcher_has_alconna_shortcuts(type(matcher)):
            return False
        if context.event_cache is not None:
            context.event_cache["route_skip"] = True
        return True
    return False


def _is_hidden_plugin(matcher: Matcher) -> bool:
    plugin = matcher.plugin
    if not plugin or not plugin.metadata:
        return False
    extra = plugin.metadata.extra or {}
    return extra.get("plugin_type") == PluginType.HIDDEN


__all__ = ["route_precheck"]

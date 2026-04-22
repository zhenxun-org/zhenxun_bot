import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from typing import Any, cast

from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.log import logger

_PATCHED = False
_ORIGINAL_FETCH: Callable[..., Awaitable[Any]] | None = None
_ORIGINAL_ONEBOT11_GROUP_MESSAGE: Callable[..., Awaitable[dict[str, Any]]] | None = None


def _sender_value(sender: Any, key: str, default: Any = None) -> Any:
    value = getattr(sender, key, default)
    return default if value is None else value


def _event_value(event: Event, key: str, default: Any = None) -> Any:
    value = getattr(event, key, default)
    return default if value is None else value


def _event_group_name(event: Event) -> str | None:
    group_name = _event_value(event, "group_name")
    if isinstance(group_name, str) and group_name:
        return group_name
    group = _event_value(event, "group")
    if group is not None:
        name = _sender_value(group, "name") or _sender_value(group, "group_name")
        if isinstance(name, str) and name:
            return name
    return None


def _has_compatible_onebot11_sender(event: Event) -> bool:
    if getattr(event, "_zx_uninfo_full_fetch", False):
        return False
    sender = _event_value(event, "sender")
    if sender is None:
        return False
    return (
        _event_value(event, "user_id") is not None
        and _event_value(event, "group_id") is not None
        and _sender_value(sender, "nickname") is not None
        and _sender_value(sender, "role") is not None
    )


async def _fast_onebot11_group_message(bot: Bot, event: Event) -> dict[str, Any]:
    """Build Uninfo session data from OneBot v11 group message event fields.

    nonebot-plugin-uninfo's default OneBot v11 fetcher always calls
    get_group_info and get_group_member_info for group messages. For normal
    matcher rule checks, event-provided sender fields are enough and avoid
    multiplying protocol API calls by the number of candidate matchers.
    """

    original = _ORIGINAL_ONEBOT11_GROUP_MESSAGE
    if not _has_compatible_onebot11_sender(event):
        if original is not None:
            return await original(bot, event)
        logger.debug("Uninfo OneBot11 fast fetch fallback unavailable")

    sender = _event_value(event, "sender")
    user_id = str(_event_value(event, "user_id", ""))
    group_id = str(_event_value(event, "group_id", ""))
    nickname = _sender_value(sender, "nickname", "")
    card = _sender_value(sender, "card", "") or nickname
    return {
        "group_id": group_id,
        "group_name": _event_group_name(event),
        "user_id": user_id,
        "name": nickname,
        "nickname": card,
        "card": card,
        "role": _sender_value(sender, "role", "member"),
        "join_time": _event_value(event, "join_time"),
        "gender": _sender_value(sender, "sex", "unknown") or "unknown",
    }


async def _singleflight_fetch(self: Any, bot: Bot, event: Event) -> Any:
    original = _ORIGINAL_FETCH
    if original is None:
        return None

    try:
        sess_id = self.get_session_id(event)
    except ValueError:
        return await original(self, bot, event)

    session_cache = getattr(self, "session_cache", None)
    if isinstance(session_cache, dict) and sess_id in session_cache:
        return session_cache[sess_id]

    inflight = getattr(self, "_zx_fetch_inflight", None)
    if not isinstance(inflight, dict):
        inflight = {}
        setattr(self, "_zx_fetch_inflight", inflight)

    key = (str(getattr(bot, "self_id", "")), event.__class__, sess_id)
    task = inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(original(self, bot, event))
        inflight[key] = task
    try:
        return await task
    finally:
        if inflight.get(key) is task and task.done():
            inflight.pop(key, None)


def apply_uninfo_onebot11_patch() -> None:
    global _ORIGINAL_FETCH, _ORIGINAL_ONEBOT11_GROUP_MESSAGE, _PATCHED
    if _PATCHED:
        return

    with contextlib.suppress(Exception):
        from nonebot_plugin_uninfo.adapters.onebot11.main import fetcher

        original_endpoint = fetcher.endpoint.get(GroupMessageEvent)
        if not getattr(original_endpoint, "__zhenxun_fast_onebot11__", False):
            _ORIGINAL_ONEBOT11_GROUP_MESSAGE = cast(
                Callable[..., Awaitable[dict[str, Any]]] | None,
                original_endpoint,
            )
            setattr(_fast_onebot11_group_message, "__zhenxun_fast_onebot11__", True)
            fetcher.endpoint[GroupMessageEvent] = _fast_onebot11_group_message

    try:
        from nonebot_plugin_uninfo.fetch import InfoFetcher
    except Exception as e:
        logger.warning("Uninfo patch skipped", e=e)
        return

    original_fetch = getattr(InfoFetcher, "fetch", None)
    if getattr(original_fetch, "__zhenxun_singleflight__", False):
        _PATCHED = True
        return
    if original_fetch is None:
        return

    _ORIGINAL_FETCH = cast(Callable[..., Awaitable[Any]], original_fetch)
    setattr(_singleflight_fetch, "__zhenxun_singleflight__", True)
    setattr(InfoFetcher, "fetch", _singleflight_fetch)
    _PATCHED = True
    logger.debug("Uninfo OneBot11 fast fetch and singleflight patch applied")

import asyncio
import contextlib
import time

from nonebot import get_driver
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.matcher import Matcher
from nonebot.message import event_preprocessor, run_postprocessor, run_preprocessor
from nonebot.typing import T_State
from nonebot_plugin_alconna import UniMsg
from nonebot_plugin_uninfo import Uninfo

from zhenxun.services.cache.runtime_cache import is_cache_ready
from zhenxun.services.log import logger
from zhenxun.services.message_load import is_overloaded
from zhenxun.services.runtime_bootstrap import register_runtime_bootstrap
from zhenxun.utils.utils import get_entity_ids

from .auth.config import LOGGER_COMMAND
from .auth_checker import (
    LimitManager,
    _get_event_cache,
    _get_route_context,
    auth,
    route_precheck,
    start_auth_runtime_tasks,
    stop_auth_runtime_tasks,
)

_SKIP_AUTH_PLUGINS = {"chat_history", "chat_message"}
_BOT_CONNECT_TS: float | None = None
_AUTH_QUEUE_MAXSIZE = 200
_AUTH_QUEUE: asyncio.Queue[tuple[Matcher, Event, Bot, Uninfo, UniMsg]] = asyncio.Queue(
    maxsize=_AUTH_QUEUE_MAXSIZE
)
_AUTH_QUEUE_STARTED = False
_AUTH_WORKERS: list[asyncio.Task] = []
_LAST_DROP_LOG = 0.0

driver = get_driver()
register_runtime_bootstrap(driver)


@driver.on_bot_connect
async def _mark_bot_connected(bot: Bot):
    del bot
    global _BOT_CONNECT_TS
    _BOT_CONNECT_TS = time.time()


async def _auth_worker(worker_id: int) -> None:
    while True:
        matcher, event, bot, session, message = await _AUTH_QUEUE.get()
        try:
            await auth(
                matcher,
                event,
                bot,
                session,
                message,
                skip_ban=True,
            )
        except IgnoredException:
            pass
        except Exception as exc:
            if not is_overloaded():
                logger.error("async auth failed", LOGGER_COMMAND, e=exc)
        finally:
            _AUTH_QUEUE.task_done()


@driver.on_startup
async def _start_auth_queue():
    global _AUTH_QUEUE_STARTED
    if _AUTH_QUEUE_STARTED:
        return
    _AUTH_QUEUE_STARTED = True
    worker_count = max(1, min(6, _AUTH_QUEUE_MAXSIZE // 50))
    for idx in range(worker_count):
        _AUTH_WORKERS.append(asyncio.create_task(_auth_worker(idx)))
    await start_auth_runtime_tasks()


@driver.on_shutdown
async def _stop_auth_queue():
    global _AUTH_QUEUE_STARTED
    _AUTH_QUEUE_STARTED = False
    workers = _AUTH_WORKERS.copy()
    _AUTH_WORKERS.clear()
    for task in workers:
        task.cancel()
    if workers:
        await asyncio.gather(*workers, return_exceptions=True)

    while not _AUTH_QUEUE.empty():
        with contextlib.suppress(Exception):
            _AUTH_QUEUE.get_nowait()
            _AUTH_QUEUE.task_done()

    await stop_auth_runtime_tasks()


def _skip_auth_for_plugin(matcher: Matcher) -> bool:
    if not matcher.plugin:
        return False
    name = (matcher.plugin.name or "").lower()
    if name in _SKIP_AUTH_PLUGINS:
        return True
    module_name = getattr(matcher.plugin, "module_name", "") or ""
    return "chat_history" in module_name


@event_preprocessor
async def _drop_message_before_cache_ready(event: Event):
    if event.get_type() != "message":
        return
    if not is_cache_ready():
        raise IgnoredException("cache not ready ignore")
    if _BOT_CONNECT_TS is not None:
        event_ts = getattr(event, "time", None)
        if event_ts is not None and event_ts < _BOT_CONNECT_TS:
            raise IgnoredException("drop backlog message")


@run_preprocessor
async def _auth_preprocessor(
    matcher: Matcher,
    event: Event,
    bot: Bot,
    session: Uninfo,
    message: UniMsg,
    state: T_State,
):
    if event.get_type() == "message" and not is_cache_ready():
        raise IgnoredException("cache not ready ignore")
    start_time = time.time()
    entity = state.get("_zx_entity")
    if entity is None:
        entity = get_entity_ids(session)
        state["_zx_entity"] = entity

    event_cache = state.get("_zx_event_cache")
    if event_cache is None:
        event_cache = _get_event_cache(event, session, entity)
        state["_zx_event_cache"] = event_cache

    text = state.get("_zx_plain_text")
    if text is None:
        text = message.extract_plain_text()
        state["_zx_plain_text"] = text
        if event_cache is not None:
            event_cache["plain_text"] = text

    route_modules = state.get("_zx_route_modules")
    if route_modules is None:
        route_modules = await _get_route_context(text, event_cache)
        state["_zx_route_modules"] = route_modules

    is_superuser = state.get("_zx_is_superuser")
    if is_superuser is None:
        is_superuser = session.user.id in bot.config.superusers
        state["_zx_is_superuser"] = is_superuser

    if await route_precheck(
        matcher,
        event,
        session,
        message,
        entity=entity,
        event_cache=event_cache,
        text=text,
        route_modules=route_modules,
    ):
        return
    if _skip_auth_for_plugin(matcher):
        return

    try:
        await auth(
            matcher,
            event,
            bot,
            session,
            message,
            skip_ban=False,
            entity=entity,
            event_cache=event_cache,
            text=text,
            route_modules=route_modules,
            is_superuser=is_superuser,
        )
    except IgnoredException:
        raise
    except Exception as exc:
        logger.error("auth check failed", LOGGER_COMMAND, e=exc)
        raise IgnoredException("auth failed") from exc

    now = time.monotonic()
    last_log = getattr(_auth_preprocessor, "_last_log", 0.0)
    if now - last_log > 1.0 and not is_overloaded():
        setattr(_auth_preprocessor, "_last_log", now)
        logger.debug(
            f"auth check cost: {time.time() - start_time:.3f}s",
            LOGGER_COMMAND,
        )


@run_postprocessor
async def _unblock_after_matcher(matcher: Matcher, session: Uninfo):
    user_id = session.user.id
    group_id = None
    channel_id = None
    if session.group:
        if session.group.parent:
            group_id = session.group.parent.id
            channel_id = session.group.id
        else:
            group_id = session.group.id
    if user_id and matcher.plugin:
        module = matcher.plugin.name
        LimitManager.unblock(module, user_id, group_id, channel_id)

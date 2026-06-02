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
from zhenxun.services.message_load import is_overloaded, mark_activity
from zhenxun.services.runtime_bootstrap import register_runtime_bootstrap

from .auth.config import LOGGER_COMMAND
from .auth.context import (
    get_event_context,
    get_or_create_event_context,
    get_permission_side_effect_cache,
    resolve_actor_user_id,
    resolve_event_channel_id,
    resolve_event_group_id,
    set_route_modules,
)
from .auth_checker import (
    LimitManager,
    _get_route_context,
    auth,
    start_auth_runtime_tasks,
    stop_auth_runtime_tasks,
)

_SKIP_AUTH_PLUGINS = {"chat_history", "chat_message"}
_BOT_CONNECT_TS: float | None = None

driver = get_driver()
register_runtime_bootstrap(driver)


@driver.on_bot_connect
async def _mark_bot_connected(bot: Bot):
    del bot
    global _BOT_CONNECT_TS
    _BOT_CONNECT_TS = time.time()


@driver.on_startup
async def _start_auth_runtime_tasks():
    await start_auth_runtime_tasks()


@driver.on_shutdown
async def _stop_auth_runtime_tasks():
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
    mark_activity()
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
    state: T_State,
    message: UniMsg | None = None,
):
    if event.get_type() == "message" and not is_cache_ready():
        raise IgnoredException("cache not ready ignore")

    # 提前判断是否跳过权限检查
    if _skip_auth_for_plugin(matcher):
        return

    start_time = time.time()
    event_context = get_or_create_event_context(
        bot,
        event,
        session,
        state,
        message=message,
    )

    if not event_context.route_modules_loaded:
        route_modules = await _get_route_context(
            event_context.plain_text,
            event_context.event_cache,
        )
        set_route_modules(state, event_context, route_modules)

    try:
        await auth(
            matcher,
            event,
            bot,
            session,
            context=event_context,
            skip_ban=False,
            state=state,
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
async def _unblock_after_matcher(
    matcher: Matcher,
    session: Uninfo,
    event: Event,
    state: T_State,
    exception: Exception | None = None,
):
    context = get_event_context(state)
    if context is not None:
        user_id = context.user_id
        group_id = context.group_id
        channel_id = context.channel_id
    else:
        user_id = resolve_actor_user_id(event, session.user.id)
        group_id = resolve_event_group_id(event, None)
        channel_id = resolve_event_channel_id(event, None)
        if session.group:
            if session.group.parent:
                group_id = session.group.parent.id
                channel_id = session.group.id
            else:
                group_id = session.group.id
    if user_id and matcher.plugin:
        module = matcher.plugin.name
        side_effects = get_permission_side_effect_cache(
            state=state,
            event_cache=context.event_cache if context is not None else None,
        )
        commit = side_effects.commits.get(module)
        if (
            commit is not None
            and not commit.committed
            and commit.owner_matcher_id == id(matcher)
        ):
            side_effects.commits.pop(module, None)
            if exception is None:
                try:
                    await commit.commit_all()
                    side_effects.auth_results[module] = (True, None)
                except Exception as exc:
                    await commit.rollback_all("commit_failed")
                    logger.error(
                        "auth side effect commit failed",
                        LOGGER_COMMAND,
                        e=exc,
                    )
            else:
                await commit.rollback_all("matcher_exception")
            if commit.limit_should_auto_unblock:
                limit_entity = commit.limit_entity
                LimitManager.unblock(
                    module,
                    limit_entity.user_id if limit_entity else user_id,
                    limit_entity.group_id if limit_entity else group_id,
                    limit_entity.channel_id if limit_entity else channel_id,
                )
        else:
            LimitManager.unblock(module, user_id, group_id, channel_id)

from __future__ import annotations

from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass
import importlib
from typing import Any

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
import nonebot.message as nb_message

from zhenxun.services.log import logger
from zhenxun.services.message_load import signal_overload

from .auth.config import LOGGER_COMMAND
from .auth_activation import HandlerActivationIndex
from .auth_patch_guard import validate_handle_event_patch
from .auth_types import EventDispatchContext


@dataclass(slots=True)
class HandleEventSelectorDependencies:
    activation_index: HandlerActivationIndex
    overload_selected_threshold: int
    prepare_handle_event_state: Callable[[Event, dict], None]
    build_dispatch_context: Callable[
        [Event, dict | None],
        Awaitable[EventDispatchContext],
    ]
    activation_context_from_dispatch: Callable[[EventDispatchContext, Event], Any]
    new_dispatch_budget: Callable[[], dict[str, int]]
    dispatch_lane_for_matcher: Callable[[type[Matcher], EventDispatchContext], str]
    merge_dispatch_budget: Callable[[dict[str, int], dict[str, int]], None]
    build_matcher_state: Callable[[dict], dict]
    run_selected_matcher: Callable[..., Awaitable[None]]


_HANDLE_EVENT_PATCHED = False
_ORIGINAL_HANDLE_EVENT: Callable[..., Awaitable[None]] | None = None
_ORIGINAL_ADAPTER_HANDLE_EVENTS: dict[object, object] = {}
_MATCHER_DEADLINE_BY_LANE = {
    "system": 15.0,
    "command": 12.0,
    "temp": 15.0,
    "passive_light": 3.0,
    "fallback_ai": 5.0,
}
_DEFAULT_MATCHER_DEADLINE = 5.0


def _matcher_deadline_for_lane(lane: str) -> float:
    return _MATCHER_DEADLINE_BY_LANE.get(lane, _DEFAULT_MATCHER_DEADLINE)


def _matcher_name(matcher: type[Matcher]) -> str:
    module = str(getattr(matcher, "module", "") or "")
    lineno = str(getattr(matcher, "lineno", "") or "")
    matcher_type = str(getattr(matcher, "type", "") or "")
    name = module or matcher.__name__
    if lineno:
        name = f"{name}:{lineno}"
    if matcher_type:
        name = f"{name}<{matcher_type}>"
    return name


async def _run_matcher_with_deadline(
    anyio_mod: Any,
    coro: Awaitable[None],
    matcher: type[Matcher],
    lane: str,
) -> None:
    timeout = _matcher_deadline_for_lane(lane)
    try:
        with anyio_mod.fail_after(timeout):
            await coro
    except TimeoutError:
        signal_overload(20.0)
        logger.warning(
            "matcher dispatch timeout: "
            f"matcher={_matcher_name(matcher)}, lane={lane}, timeout={timeout:.1f}s",
            LOGGER_COMMAND,
        )


def _trim_leading_text(message: Any) -> None:
    if not message:
        return
    segment = message[0]
    if getattr(segment, "type", None) != "text":
        return
    data = getattr(segment, "data", None)
    if not isinstance(data, dict):
        return
    data["text"] = str(data.get("text", "")).lstrip("\xa0").lstrip()
    if not data["text"]:
        del message[0]


def _is_self_mention_segment(segment: Any, bot: Bot) -> bool:
    segment_type = getattr(segment, "type", None)
    if segment_type not in {"mention_user", "group_mention_user"}:
        return False
    data = getattr(segment, "data", None)
    if not isinstance(data, dict):
        return False
    if data.get("is_you") or data.get("is_bot"):
        return True
    user_id = data.get("user_id")
    return user_id is not None and str(user_id) == str(bot.self_id)


def _ensure_nonempty_qq_message(message: Any) -> None:
    if message:
        return
    with contextlib.suppress(Exception):
        message_module = importlib.import_module("nonebot.adapters.qq.message")
        MessageSegment = getattr(message_module, "MessageSegment")
        message.append(MessageSegment.text(""))


def _normalize_qq_self_at_message(bot: Bot, event: Event) -> None:
    """Remove the leading bot mention left by QQ official @ events.

    nonebot-adapter-qq's @ event branches can mark ``to_me`` but keep the
    synthetic leading mention segment. Alconna command heads then see
    ``<@bot>命令`` and fail to match, while regular ``event.get_plaintext()``
    still looks correct. Normalizing here keeps the runtime behavior aligned
    with OneBot/standard to_me preprocessing without changing plugin code or
    database state.
    """
    if event.__class__.__name__ not in {
        "AtMessageCreateEvent",
        "GroupAtMessageCreateEvent",
    }:
        return
    adapter = getattr(bot, "adapter", None)
    adapter_name = ""
    get_name = getattr(adapter, "get_name", None)
    if callable(get_name):
        with contextlib.suppress(Exception):
            adapter_name = str(get_name()).lower()
    if adapter_name != "qq":
        return
    with contextlib.suppress(Exception):
        message = event.get_message()
        if not message or not _is_self_mention_segment(message[0], bot):
            return
        message.pop(0)
        setattr(event, "to_me", True)
        _trim_leading_text(message)
        _ensure_nonempty_qq_message(message)


async def patched_handle_event(
    bot: Bot,
    event: Event,
    deps: HandleEventSelectorDependencies,
) -> None:
    _normalize_qq_self_at_message(bot, event)
    show_log = True
    escape_tag = getattr(nb_message, "escape_tag")
    logger_ = getattr(nb_message, "logger")
    no_log_exception = getattr(nb_message, "NoLogException")

    log_msg = f"<m>{escape_tag(bot.type)} {escape_tag(bot.self_id)}</m> | "
    try:
        log_msg += event.get_log_string()
    except no_log_exception:
        show_log = False
    if show_log:
        logger_.opt(colors=True).success(log_msg)

    state = {}
    dependency_cache = {}
    async_exit_stack = getattr(nb_message, "AsyncExitStack")
    apply_event_preprocessors = getattr(nb_message, "_apply_event_preprocessors")
    apply_event_postprocessors = getattr(nb_message, "_apply_event_postprocessors")
    trie_rule = getattr(nb_message, "TrieRule")
    matchers = getattr(nb_message, "matchers")
    catch = getattr(nb_message, "catch")
    stop_propagation = getattr(nb_message, "StopPropagation")
    handle_exception = getattr(nb_message, "_handle_exception")
    anyio_mod = getattr(nb_message, "anyio")

    async with async_exit_stack() as stack:
        if not await apply_event_preprocessors(
            bot=bot,
            event=event,
            state=state,
            stack=stack,
            dependency_cache=dependency_cache,
        ):
            return

        try:
            trie_rule.get_value(bot, event, state)
        except Exception as e:
            logger_.opt(colors=True, exception=e).warning(
                "Error while parsing command for event"
            )
        deps.prepare_handle_event_state(event, state)
        dispatch_context = await deps.build_dispatch_context(event, state)
        activation_context = deps.activation_context_from_dispatch(
            dispatch_context,
            event,
        )
        activation_available = True
        try:
            deps.activation_index.ensure_fresh(matchers)
        except Exception as exc:
            activation_available = False
            logger.warning(
                "HandlerActivationIndex 构建失败，回退到旧 matcher 选择逻辑",
                LOGGER_COMMAND,
                e=exc,
            )

        break_flag = False

        def _handle_stop_propagation(_exc_group) -> None:
            nonlocal break_flag
            break_flag = True
            logger_.debug("Stop event propagation")

        for priority in sorted(matchers.keys()):
            if break_flag:
                break

            if show_log:
                logger_.debug(f"Checking for matchers in priority {priority}...")

            if not (priority_matchers := matchers[priority]):
                continue

            with catch(
                {
                    stop_propagation: _handle_stop_propagation,
                    Exception: handle_exception(
                        "<r><bg #f8bbd0>Error when checking Matcher.</bg #f8bbd0></r>"
                    ),
                }
            ):
                priority_budget = deps.new_dispatch_budget()
                if activation_available:
                    try:
                        activation_result = deps.activation_index.select_priority(
                            priority,
                            priority_matchers,
                            activation_context,
                            priority_budget,
                        )
                    except Exception as exc:
                        logger.warning(
                            "HandlerActivationIndex 选择失败，当前 priority 回退",
                            LOGGER_COMMAND,
                            e=exc,
                        )
                        activation_result = None
                else:
                    activation_result = None

                if activation_result is not None:
                    selected_matchers = activation_result.selected
                    if (
                        activation_result.candidate_count
                        > deps.overload_selected_threshold
                    ):
                        signal_overload(3.0)
                else:
                    selected_matchers = priority_matchers

                async with anyio_mod.create_task_group() as tg:
                    for matcher in selected_matchers:
                        lane = deps.dispatch_lane_for_matcher(matcher, dispatch_context)
                        if activation_result is None:
                            descriptor = deps.activation_index.descriptor_for(matcher)
                            if descriptor is not None:
                                single_budget = dict(priority_budget)
                                try:
                                    single_result = (
                                        deps.activation_index.select_priority(
                                            priority,
                                            [matcher],
                                            activation_context,
                                            single_budget,
                                        )
                                    )
                                except Exception:
                                    single_result = None
                                if single_result is not None:
                                    deps.merge_dispatch_budget(
                                        priority_budget,
                                        single_budget,
                                    )
                                    if not single_result.selected:
                                        continue
                        matcher_state = deps.build_matcher_state(state)
                        tg.start_soon(
                            _run_matcher_with_deadline,
                            anyio_mod,
                            deps.run_selected_matcher(
                                matcher,
                                bot,
                                event,
                                matcher_state,
                                stack,
                                dependency_cache,
                                lane,
                            ),
                            matcher,
                            lane,
                        )

        if show_log:
            logger_.debug("Checking for matchers completed")

        await apply_event_postprocessors(bot, event, state, stack, dependency_cache)


def install_handle_event_selector(deps: HandleEventSelectorDependencies) -> None:
    global _HANDLE_EVENT_PATCHED, _ORIGINAL_HANDLE_EVENT
    if _HANDLE_EVENT_PATCHED:
        return
    guard = validate_handle_event_patch()
    if not guard.ok:
        logger.warning(
            f"权限事件分发选择器 patch 未安装，回退 NoneBot 原生分发: {guard.reason}",
            LOGGER_COMMAND,
        )
        return
    _ORIGINAL_HANDLE_EVENT = nb_message.handle_event

    async def _patched(bot: Bot, event: Event) -> None:
        await patched_handle_event(bot, event, deps)

    nb_message.handle_event = _patched  # type: ignore[assignment]
    for module_name in (
        "nonebot.adapters.onebot.v11.bot",
        "nonebot.adapters.onebot.v12.bot",
        "nonebot.adapters.qq.bot",
        "onebug.mixin.process",
    ):
        with contextlib.suppress(Exception):
            module = importlib.import_module(module_name)
            current = getattr(module, "handle_event", None)
            if current is not None:
                _ORIGINAL_ADAPTER_HANDLE_EVENTS[module] = current
                setattr(module, "handle_event", _patched)
    _HANDLE_EVENT_PATCHED = True


def uninstall_handle_event_selector() -> None:
    global _HANDLE_EVENT_PATCHED, _ORIGINAL_HANDLE_EVENT
    if not _HANDLE_EVENT_PATCHED:
        return
    if _ORIGINAL_HANDLE_EVENT is not None:
        nb_message.handle_event = _ORIGINAL_HANDLE_EVENT  # type: ignore[assignment]
    for module, original in list(_ORIGINAL_ADAPTER_HANDLE_EVENTS.items()):
        with contextlib.suppress(Exception):
            setattr(module, "handle_event", original)
    _ORIGINAL_ADAPTER_HANDLE_EVENTS.clear()
    _HANDLE_EVENT_PATCHED = False
    _ORIGINAL_HANDLE_EVENT = None


__all__ = [
    "HandleEventSelectorDependencies",
    "install_handle_event_selector",
    "patched_handle_event",
    "uninstall_handle_event_selector",
]

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
    record_activation_result: Callable[[Any], None]
    debug_activation_shadow: Callable[..., None]
    merge_dispatch_budget: Callable[[dict[str, int], dict[str, int]], None]
    build_matcher_state: Callable[[dict], dict]
    run_selected_matcher: Callable[..., Awaitable[None]]


_HANDLE_EVENT_PATCHED = False
_ORIGINAL_HANDLE_EVENT: Callable[..., Awaitable[None]] | None = None
_ORIGINAL_ADAPTER_HANDLE_EVENTS: dict[object, object] = {}


async def patched_handle_event(
    bot: Bot,
    event: Event,
    deps: HandleEventSelectorDependencies,
) -> None:
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
    run_coro_with_shield = getattr(nb_message, "run_coro_with_shield")

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
                    deps.record_activation_result(activation_result)
                    deps.debug_activation_shadow(
                        priority=priority,
                        activation_result=activation_result,
                        context=dispatch_context,
                    )
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
                                    deps.record_activation_result(single_result)
                                    deps.debug_activation_shadow(
                                        priority=priority,
                                        activation_result=single_result,
                                        context=dispatch_context,
                                    )
                                    deps.merge_dispatch_budget(
                                        priority_budget,
                                        single_budget,
                                    )
                                    if not single_result.selected:
                                        continue
                        matcher_state = deps.build_matcher_state(state)
                        tg.start_soon(
                            run_coro_with_shield,
                            deps.run_selected_matcher(
                                matcher,
                                bot,
                                event,
                                matcher_state,
                                stack,
                                dependency_cache,
                                lane,
                            ),
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

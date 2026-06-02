from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any

from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher
from nonebot_plugin_uninfo import Uninfo

from zhenxun.utils.utils import EntityIDs

from .auth.context import (
    EventContext,
    PermissionSideEffectCache,
    set_route_modules,
)
from .auth.exception import PermissionExemption, SkipPluginException
from .auth_policy import (
    action_from_snapshot,
    principal_from_snapshot,
    raise_for_policy,
    resource_from_snapshot,
)
from .auth_types import AuthLaneContext, AuthPolicyFlags, AuthPreparation

if TYPE_CHECKING:
    from .auth_side_effect import SideEffectCommit
    from .auth_trace import HookTraceRecorder


def _require(value: Any, name: str):
    if value is None:
        raise RuntimeError(f"AuthPipelineContext.{name} is required")
    return value


def _prep(ctx: AuthPipelineContext) -> AuthPreparation:
    return _require(ctx.prep, "prep")


def _recorder(ctx: AuthPipelineContext) -> "HookTraceRecorder":
    return _require(ctx.hook_recorder, "hook_recorder")


def _side_effect_commit(ctx: AuthPipelineContext) -> "SideEffectCommit":
    return _require(ctx.side_effect_commit, "side_effect_commit")


def _side_effect_cache(ctx: AuthPipelineContext) -> PermissionSideEffectCache:
    return _require(ctx.side_effect_cache, "side_effect_cache")


def _entity(ctx: AuthPipelineContext) -> EntityIDs:
    return _require(ctx.entity, "entity")


def _lane_context(ctx: AuthPipelineContext) -> AuthLaneContext:
    return _require(ctx.lane_context, "lane_context")


PipelineHandler = Callable[["AuthPipelineContext"], Awaitable[None]]


@dataclass(slots=True)
class AuthPipelineStage:
    name: str
    handler: PipelineHandler


@dataclass(slots=True)
class AuthPipelineContext:
    matcher: Matcher
    event: Event
    bot: Bot
    session: Uninfo
    event_context: EventContext
    skip_ban: bool = False
    state: dict | None = None
    start_time: float = field(default_factory=time.time)
    module: str = ""
    entity: EntityIDs | None = None
    event_cache: dict | None = None
    text: str = ""
    route_modules: set[str] | None = None
    is_command_matcher: bool = False
    lane_context: AuthLaneContext | None = None
    side_effect_cache: PermissionSideEffectCache | None = None
    side_effect_commit: "SideEffectCommit | None" = None
    side_effect_lock: asyncio.Lock | None = None
    entered_side_effect_lock: bool = False
    auth_result_cache: dict | None = None
    hook_recorder: "HookTraceRecorder | None" = None
    prep: AuthPreparation | None = None
    flags: AuthPolicyFlags | None = None
    cost_gold: int = 0
    hooks_time: float = 0.0
    ignore_flag: bool = False
    auth_allowed: bool | None = None
    decision_effect: str | None = None
    decision_reason: str | None = None
    stopped: bool = False
    stage_timings: dict[str, float] = field(default_factory=dict)

    def stop(
        self,
        *,
        allowed: bool,
        effect: str,
        reason: str,
    ) -> None:
        self.auth_allowed = allowed
        self.decision_effect = effect
        self.decision_reason = reason
        self.stopped = True


class AuthPipeline:
    def __init__(self, stages: list[AuthPipelineStage]) -> None:
        self._stages = tuple(stages)

    async def run(self, context: AuthPipelineContext) -> None:
        for stage in self._stages:
            started = time.perf_counter()
            await stage.handler(context)
            context.stage_timings[stage.name] = (time.perf_counter() - started) * 1000
            if context.stopped:
                break


@dataclass(slots=True)
class AuthPipelineDependencies:
    route_modules_with_commands: set[str]
    get_route_context: Callable[[str, dict | None], Awaitable[set[str]]]
    is_hidden_plugin: Callable[[Matcher], bool]
    is_command_matcher_class: Callable[[type[Matcher]], bool]
    matcher_has_alconna_shortcuts: Callable[[type[Matcher]], bool]
    prepare_auth_state_with_fallback: Callable[..., Awaitable[Any]]
    prepare_auth_state: Callable[..., Awaitable[Any]]
    policy_decision_point: Any
    policy_skip_message: Callable[[str], str]
    legacy_pure_auth_fallback: Callable[..., Awaitable[None]]
    check_ban_from_snapshot: Callable[..., Awaitable[None]]
    resolve_cost_gold: Callable[..., Awaitable[int]]
    run_auth_hooks: Callable[..., Awaitable[float]]
    bot_filter: Callable[..., None]
    reserve_gold: Callable[..., Awaitable[Any]]
    insufficient_gold_error: type[Exception]
    logger: Any
    log_command: str


def apply_policy_precheck(
    ctx: AuthPipelineContext,
    deps: AuthPipelineDependencies,
) -> AuthPolicyFlags:
    prep = _prep(ctx)
    hook_recorder = _recorder(ctx)
    flags = AuthPolicyFlags()
    snapshot = prep.snapshot
    decision = deps.policy_decision_point.decide(
        principal_from_snapshot(snapshot),
        action_from_snapshot(snapshot),
        resource_from_snapshot(snapshot),
        prep.policy_context,
    )
    if decision.deferred:
        hook_recorder.set("auth_core", f"policy:{decision.reason}")
    if decision.denied:
        raise_for_policy(decision, deps.policy_skip_message(decision.reason))
    if decision.allowed and decision.reason in {"hidden_plugin_skip_auth"}:
        flags.should_return_allowed = True
        return flags

    bot_decision = deps.policy_decision_point.decide_bot(prep.policy_context)
    if bot_decision.allowed:
        hook_recorder.set("auth_bot", "policy")
    elif bot_decision.denied:
        raise_for_policy(bot_decision, deps.policy_skip_message(bot_decision.reason))
    elif bot_decision.deferred:
        raise PermissionExemption(f"auth_bot deferred: {bot_decision.reason}")

    group_decision = deps.policy_decision_point.decide_group(prep.policy_context)
    if group_decision.allowed or group_decision.skipped:
        hook_recorder.set("auth_group", f"policy:{group_decision.reason}")
    elif group_decision.denied:
        raise_for_policy(
            group_decision,
            deps.policy_skip_message(group_decision.reason),
        )
    elif group_decision.deferred:
        raise PermissionExemption(f"auth_group deferred: {group_decision.reason}")

    plugin_decision = deps.policy_decision_point.decide_plugin(prep.policy_context)
    if plugin_decision.allowed or plugin_decision.skipped:
        hook_recorder.set("auth_plugin", f"policy:{plugin_decision.reason}")
    elif plugin_decision.denied:
        raise_for_policy(
            plugin_decision,
            deps.policy_skip_message(plugin_decision.reason),
        )
    else:
        raise PermissionExemption(f"auth_plugin deferred: {plugin_decision.reason}")

    admin_decision = deps.policy_decision_point.decide_admin(prep.policy_context)
    if admin_decision.allowed or admin_decision.skipped:
        hook_recorder.set("auth_admin", f"policy:{admin_decision.reason}")
    elif admin_decision.denied:
        raise_for_policy(
            admin_decision,
            deps.policy_skip_message(admin_decision.reason),
        )
    else:
        raise PermissionExemption(f"auth_admin deferred: {admin_decision.reason}")

    return flags


async def route_gate_stage(
    ctx: AuthPipelineContext,
    deps: AuthPipelineDependencies,
) -> None:
    if not ctx.module:
        ctx.stop(allowed=True, effect="allow", reason="empty_module")
        return

    side_effect_cache = _side_effect_cache(ctx)
    ctx.side_effect_lock = side_effect_cache.lock_for(ctx.module)
    await ctx.side_effect_lock.acquire()
    ctx.entered_side_effect_lock = True

    auth_result_cache = side_effect_cache.auth_results
    ctx.auth_result_cache = auth_result_cache
    cached_result = auth_result_cache.get(ctx.module)
    if cached_result is not None:
        allowed, reason = cached_result
        if not allowed:
            ctx.decision_effect = "skip"
            ctx.decision_reason = reason or "auth_cached_skip"
            raise SkipPluginException(reason or "auth cached skip")
        ctx.stop(allowed=True, effect="allow", reason="auth_cached_allow")
        return

    if deps.is_hidden_plugin(ctx.matcher):
        ctx.stop(allowed=True, effect="allow", reason="hidden_plugin")
        return
    if ctx.event_cache is not None and ctx.event_cache.get("ban_state") is True:
        ctx.decision_effect = "skip"
        ctx.decision_reason = "ban_cached"
        raise SkipPluginException("user or group banned (cached)")

    if ctx.route_modules is None:
        ctx.route_modules = await deps.get_route_context(ctx.text, ctx.event_cache)
        set_route_modules(ctx.state, ctx.event_context, ctx.route_modules)
    route_missed = (
        ctx.is_command_matcher
        and ctx.module in deps.route_modules_with_commands
        and ctx.module not in ctx.route_modules
        and not deps.matcher_has_alconna_shortcuts(type(ctx.matcher))
    )
    if route_missed:
        if ctx.event_cache is not None:
            ctx.event_cache["route_miss_after_native_match"] = True
        _recorder(ctx).set("route", "miss")


async def prepare_snapshot_stage(
    ctx: AuthPipelineContext,
    deps: AuthPipelineDependencies,
) -> None:
    ctx.prep = await deps.prepare_auth_state_with_fallback(
        module=ctx.module,
        context=ctx.event_context,
        bot=ctx.bot,
        event_cache=ctx.event_cache,
        skip_ban=ctx.skip_ban,
        hook_recorder=ctx.hook_recorder,
        state=ctx.state,
        session=ctx.session,
    )
    if ctx.prep is None:
        ctx.stop(allowed=True, effect="allow", reason="prepare_timeout_allow")


async def policy_precheck_stage(
    ctx: AuthPipelineContext,
    deps: AuthPipelineDependencies,
) -> None:
    try:
        ctx.flags = apply_policy_precheck(ctx, deps)
    except PermissionExemption as exc:
        _recorder(ctx).set("policy_fallback", str(exc))
        ctx.prep = await deps.prepare_auth_state(
            module=ctx.module,
            context=ctx.event_context,
            bot=ctx.bot,
            event_cache=ctx.event_cache,
            skip_ban=ctx.skip_ban,
            hook_recorder=ctx.hook_recorder,
            state=ctx.state,
            session=ctx.session,
            allow_cache_load=True,
        )
        if ctx.prep is None:
            ctx.stop(allowed=True, effect="allow", reason="policy_fallback_timeout")
            return
        try:
            ctx.flags = apply_policy_precheck(ctx, deps)
        except PermissionExemption as fallback_exc:
            _recorder(ctx).set("legacy_pure_auth", str(fallback_exc))
            await deps.legacy_pure_auth_fallback(
                prep=ctx.prep,
                event=ctx.event,
                session=ctx.session,
                text=ctx.text,
            )
            ctx.flags = AuthPolicyFlags()
    flags = _require(ctx.flags, "flags")
    if flags.should_return_allowed:
        ctx.stop(allowed=True, effect="allow", reason="policy_precheck_allow")
        return
    await deps.check_ban_from_snapshot(
        prep=ctx.prep,
        matcher=ctx.matcher,
        event_cache=ctx.event_cache,
        skip_ban=ctx.skip_ban,
        hook_recorder=ctx.hook_recorder,
        session=ctx.session,
    )
    ctx.cost_gold = await deps.resolve_cost_gold(
        prep=ctx.prep,
        hook_recorder=ctx.hook_recorder,
        session=ctx.session,
    )


async def legacy_hook_adapter_stage(
    ctx: AuthPipelineContext,
    deps: AuthPipelineDependencies,
) -> None:
    prep = _prep(ctx)
    deps.bot_filter(ctx.session, context=prep.permission_context)
    ctx.hooks_time = await deps.run_auth_hooks(
        prep=prep,
        session=ctx.session,
        event_cache=ctx.event_cache,
        lane_context=_lane_context(ctx),
        hook_recorder=_recorder(ctx),
        side_effect_commit=_side_effect_commit(ctx),
    )
    ctx.auth_allowed = True
    ctx.decision_effect = "allow"
    ctx.decision_reason = "auth_passed"


async def side_effect_commit_stage(
    ctx: AuthPipelineContext,
    deps: AuthPipelineDependencies,
) -> None:
    commit = _side_effect_commit(ctx)
    side_effect_cache = _side_effect_cache(ctx)
    if ctx.ignore_flag:
        await commit.rollback_all("auth_ignored")
        return
    if ctx.cost_gold <= 0:
        if commit.has_pending:
            side_effect_cache.commits[ctx.module] = commit
        return
    gold_start = time.time()
    try:
        reservation = await deps.reserve_gold(
            _entity(ctx).user_id,
            ctx.module,
            ctx.cost_gold,
            ctx.session,
        )
        await commit.reserve_gold(
            reservation,
            amount=ctx.cost_gold,
            metadata={"module": ctx.module},
        )
        _recorder(ctx).set("reserve_gold", f"{time.time() - gold_start:.3f}s")
    except deps.insufficient_gold_error:
        deps.logger.debug(
            f"预扣金币失败，金币不足: {ctx.module}",
            deps.log_command,
            session=ctx.session,
        )
        raise SkipPluginException(f"{ctx.module} 金币不足，已取消执行...") from None
    except TimeoutError:
        deps.logger.error(
            f"预扣金币超时，模块: {ctx.module}",
            deps.log_command,
            session=ctx.session,
        )
        raise
    side_effect_cache.commits[ctx.module] = commit


async def decision_log_stage(
    ctx: AuthPipelineContext,
    deps: AuthPipelineDependencies,
) -> None:
    commit = ctx.side_effect_commit
    has_deferred_commit = commit is not None and commit.has_pending
    if (
        ctx.auth_result_cache is not None
        and ctx.auth_allowed is not None
        and not has_deferred_commit
    ):
        ctx.auth_result_cache[ctx.module] = (
            ctx.auth_allowed,
            None if ctx.auth_allowed else ctx.decision_reason,
        )
    if ctx.entered_side_effect_lock and ctx.side_effect_lock is not None:
        try:
            ctx.side_effect_lock.release()
        except Exception:
            pass
        ctx.entered_side_effect_lock = False


def build_auth_pipeline(deps: AuthPipelineDependencies) -> AuthPipeline:
    return AuthPipeline(
        [
            AuthPipelineStage("route_gate", lambda ctx: route_gate_stage(ctx, deps)),
            AuthPipelineStage(
                "prepare_snapshot",
                lambda ctx: prepare_snapshot_stage(ctx, deps),
            ),
            AuthPipelineStage(
                "policy_precheck",
                lambda ctx: policy_precheck_stage(ctx, deps),
            ),
            AuthPipelineStage(
                "legacy_hook_adapter",
                lambda ctx: legacy_hook_adapter_stage(ctx, deps),
            ),
            AuthPipelineStage(
                "side_effect_commit",
                lambda ctx: side_effect_commit_stage(ctx, deps),
            ),
        ]
    )

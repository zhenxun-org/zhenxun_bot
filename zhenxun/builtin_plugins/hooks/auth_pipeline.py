from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time
from typing import Any

PipelineHandler = Callable[["AuthPipelineContext"], Awaitable[None]]


@dataclass(slots=True)
class AuthPipelineStage:
    name: str
    handler: PipelineHandler


@dataclass(slots=True)
class AuthPipelineContext:
    matcher: Any
    event: Any
    bot: Any
    session: Any
    event_context: Any
    skip_ban: bool = False
    state: dict | None = None
    start_time: float = field(default_factory=time.time)
    module: str = ""
    entity: Any = None
    event_cache: dict | None = None
    text: str = ""
    route_modules: set[str] | None = None
    route_skip_checks: bool = False
    is_command_matcher: bool = False
    lane_context: Any = None
    side_effect_cache: Any = None
    side_effect_commit: Any = None
    side_effect_lock: Any = None
    entered_side_effect_lock: bool = False
    auth_result_cache: dict | None = None
    hook_recorder: Any = None
    prep: Any = None
    flags: Any = None
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

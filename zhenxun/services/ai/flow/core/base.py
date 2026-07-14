from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import AsyncIterator
import contextlib
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast

from nonebot.params import Depends

from zhenxun.services.ai.core.exceptions import (
    ConcurrencyInterruptException,
    ControlFlowExit,
)
from zhenxun.services.ai.core.models import CancellationToken
from zhenxun.services.ai.core.stream_events import AgentStreamEvent, EventBus
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.run.models import AgentRunError, RunIntent, StreamedRunResult
from zhenxun.services.ai.run.subscribers import DefaultUISubscriber, TelemetrySubscriber
from zhenxun.services.ai.run.ui import UIController
from zhenxun.services.ai.utils import ContextUtils
from zhenxun.services.ai.utils.logger import log_flow as logger

if TYPE_CHECKING:
    from zhenxun.services.ai.flow.agent.models import Persona

from zhenxun.services.ai.core.messages import PromptInput

from .models import (
    BaseRuntimeConfig,
    ConcurrencyPolicy,
)

T_RunResult = TypeVar("T_RunResult")


class BaseRunnable(ABC, Generic[T_RunResult]):
    """
    所有可执行 AI 编排实体的统一基类
    统一了 Agent, Team, Workflow 的核心契约，支持物理上的任意嵌套。
    """

    name: str
    """可执行实体的名称标识"""

    description: str
    """可执行实体的详细描述。用于外部路由(Router)或上层智能体(DelegateTool)决定是否调用它"""

    persona: "Persona | None" = None
    """(可选) 实体的角色设定 (Persona)。包含 role 和 goal，
    在多智能体路由移交时优先级最高"""

    runtime_config: BaseRuntimeConfig
    """运行时配置，如是否无状态、UI输出模式等"""

    @property
    def profile_summary(self) -> str:
        """获取该实体的标准化简要画像/描述，供上层路由和规划决策使用"""
        if self.persona:
            return f"角色：{self.persona.role}，目标：{self.persona.goal}"
        return self.description or "处理节点"

    def bind(self, **kwargs: Any) -> Any:
        """DI 注入语法糖：返回 Depends，自动绑定当前上下文"""

        from .runner import FlowRunner

        async def _dependency() -> FlowRunner[Any]:
            return FlowRunner[Any](self, **kwargs)

        return Depends(_dependency)

    async def reply(
        self,
        prompt: PromptInput | None = None,
        reply_to: bool = False,
        *,
        context: RunContext | None = None,
        **kwargs: Any,
    ) -> T_RunResult:
        """交互执行语法糖，自动渲染流式进度并最终将结果回复给终端用户"""
        from .runner import FlowRunner

        runner = FlowRunner(self, context=context, **kwargs)
        return cast(T_RunResult, await runner.reply(prompt=prompt, reply_to=reply_to))

    async def run(
        self,
        prompt: PromptInput | None = None,
        *,
        context: RunContext | None = None,
        **kwargs: Any,
    ) -> T_RunResult:
        """阻塞式核心运行入口，安全捕获内部抛出的静默退出信号"""

        try:
            async with self.run_stream(
                prompt=prompt, context=context, **kwargs
            ) as stream_result:
                return cast(T_RunResult, await stream_result.get_run_result())
        except ControlFlowExit as e:
            logger.info(f"[{self.name}] 触发底层控制流，已安全退出: {e}")

            await UIController.handle_control_flow_exit_display(e, context)

            raise asyncio.CancelledError()

    @contextlib.asynccontextmanager
    async def run_stream(
        self,
        prompt: PromptInput | None = None,
        *,
        context: RunContext | None = None,
        deps: Any = None,
        event_bus: EventBus | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamedRunResult[Any]]:
        """统一的流式运行入口，负责生命周期调度、并发锁管理和事件总线挂载。"""
        from .concurrency import apply_concurrency_policy

        intent = RunIntent.from_input(prompt)
        bus = event_bus or EventBus()

        if context is None:
            safe_context = RunContext(session_id=kwargs.get("session_id"))
            if deps is not None:
                safe_context.deps = deps
        else:
            safe_context = context
            if deps is not None and safe_context.deps is None:
                safe_context.deps = deps

        is_root = not safe_context.state.get("__is_root_run_executed__", False)
        if is_root:
            safe_context.state["__is_root_run_executed__"] = True
            TelemetrySubscriber().attach(bus)
            if (
                safe_context.get_bot()
                and safe_context.get_event()
                and safe_context.run.delegate_depth == 0
            ):
                config_obj = getattr(self, "config", self.runtime_config)
                verbose_ui = getattr(config_obj, "verbose_ui", False)
                DefaultUISubscriber(safe_context, verbose=verbose_ui).attach(bus)

        policy = getattr(self.runtime_config, "concurrency_policy", None)
        if policy is None:
            policy = (
                ConcurrencyPolicy.ALLOW
                if getattr(self.runtime_config, "stateless", True)
                else ConcurrencyPolicy.QUEUE
            )

        intervention_policy = getattr(self.runtime_config, "intervention_policy", None)
        lock_id = ContextUtils.extract_concurrency_lock_id(
            safe_context,
            getattr(self.runtime_config, "concurrency_scope", None),
            safe_context.session_id or "default_session",
        )

        async def _execution_task():
            cancel_token = safe_context.run.cancellation_token or CancellationToken()
            safe_context.run.cancellation_token = cancel_token
            try:
                async with apply_concurrency_policy(
                    session_id=safe_context.session_id or "default_session",
                    lock_id=lock_id,
                    policy=policy,
                    cancel_token=cancel_token,
                    intervention_policy=intervention_policy,
                    intent=intent,
                ):
                    async for event in self._execute_stream(
                        intent=intent,
                        context=safe_context,
                        cancel_token=cancel_token,
                        event_bus=bus,
                        **kwargs,
                    ):
                        await bus.emit(event)
            except ControlFlowExit as e:
                await bus.emit(AgentRunError(error=e))
            except asyncio.CancelledError:
                logger.debug(f"[{self.name}] 执行被并发策略中断取消。")
                await bus.emit(
                    AgentRunError(
                        error=ConcurrencyInterruptException("任务已被新请求打断并接管")
                    )
                )
            except Exception as e:
                await bus.emit(AgentRunError(error=e))
            finally:
                await bus.end()

        task = asyncio.create_task(_execution_task())
        result_obj = StreamedRunResult[Any](bus)
        try:
            yield result_obj
        finally:
            if not task.done():
                task.cancel()

    @abstractmethod
    async def _execute_stream(
        self,
        intent: RunIntent,
        context: RunContext,
        cancel_token: CancellationToken,
        event_bus: EventBus,
        **kwargs: Any,
    ) -> AsyncIterator[AgentStreamEvent]:
        """核心执行流（由子类实现），通过 yield 返回执行事件。"""
        if False:
            yield cast(Any, None)

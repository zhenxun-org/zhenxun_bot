import asyncio
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from typing_extensions import Self

from pydantic import BaseModel

from zhenxun.services.ai.capabilities import AbstractCapability, DynamicCapability
from zhenxun.services.ai.core.exceptions import ConcurrencyInterruptException
from zhenxun.services.ai.core.messages import PromptInput
from zhenxun.services.ai.core.models import CancellationToken
from zhenxun.services.ai.core.stream_events import EventBus
from zhenxun.services.ai.flow.agent.agent import CapabilitySource, ToolSource
from zhenxun.services.ai.flow.agent.models import Persona
from zhenxun.services.ai.flow.base import BaseRunnable
from zhenxun.services.ai.flow.team.models import TeamRuntimeConfig, Transition
from zhenxun.services.ai.flow.team.router import BaseRouter
from zhenxun.services.ai.flow.team.strategy import (
    BaseTeamStrategy,
)
from zhenxun.services.ai.run import AgentRunResult, RunContext, Task
from zhenxun.services.ai.tools.providers.skills.models import Skill, SkillSource
from zhenxun.utils.utils import infer_plugin_namespace


class Team(BaseRunnable[AgentRunResult[Any]]):
    """
    多智能体动态编排与路由控制器 (Facade)。
    继承自 BaseRunnable，支持被嵌套在其他 Team 或 Workflow 中。
    """

    def __init__(
        self,
        name: str,
        members: list[BaseRunnable[Any]],
        model: str | Callable[[], str] | None = None,
        strategy: BaseTeamStrategy | None = None,
        description: str | None = None,
        persona: Persona | dict | None = None,
        runtime_config: TeamRuntimeConfig | dict | None = None,
        capabilities: list[CapabilitySource] | None = None,
        skills: Sequence[str | Path | Skill | SkillSource] | None = None,
    ):
        """
        多智能体协作团队初始化。

        参数:
            name: 团队的名称标识。
            members: 团队成员列表，可以包含 Agent、Workflow 或其他 Team。
            model: (可选) 团队的统一默认模型，将自动被内部的 Leader/Router 继承。
            strategy: (可选) 团队协作策略实例。
                若不传入，必须随后使用 `.with_xxx()` 链式方法配置。
            description: 团队的职能描述，用于上层节点路由。
            persona: 团队的整体人设或宏观设定。
            runtime_config: 团队级别的运行时宏观配置.
        """
        self.name = name
        self.members = members
        self.model = model
        self.strategy = strategy
        self.description = (
            description
            or f"一个名为 {self.name} 的协作团队，包含 {len(self.members)} 个处理节点。"
        )
        self.persona = persona

        self.namespace = infer_plugin_namespace() or "unknown"

        self.capabilities: list[Any] = []
        if capabilities:
            for cap in capabilities:
                if isinstance(cap, AbstractCapability):
                    self.capabilities.append(cap)
                elif callable(cap):
                    self.capabilities.append(DynamicCapability(cap))

        if isinstance(runtime_config, dict):
            runtime_config = TeamRuntimeConfig(**runtime_config)
        self.runtime_config = runtime_config or TeamRuntimeConfig(stateless=True)

        if skills:
            from zhenxun.services.ai.tools.providers.skills.capabilities import (
                SkillCapability,
            )

            self.capabilities.append(
                SkillCapability(skills=skills, namespace=self.namespace)
            )

        self.selector_func = (
            getattr(strategy, "selector_func", None) if strategy else None
        )

    def with_strategy(self, strategy: BaseTeamStrategy) -> Self:
        """
        挂载自定义的团队协作策略。

        该方法为第三方扩展策略提供了通用注入通道。

        参数:
            strategy: 自定义的、继承自 BaseTeamStrategy 的团队协作策略实例。
        """
        self.strategy = strategy
        self.selector_func = getattr(strategy, "selector_func", None)
        return self

    def with_routing(
        self,
        state_flow: (
            Mapping[str, Sequence[Transition | str | Any]] | Callable | None
        ) = None,
        selector_func: Callable[..., str | None] | None = None,
        router: BaseRouter | None = None,
        leader_model: str | None = None,
        leader_tools: list[ToolSource] | None = None,
        custom_prompt: str | None = None,
    ) -> Self:
        """
        应用路由策略，基于挂载的 Router 进行最合适的专家动态分发。

        路由策略初始化，通过决策大脑动态路由，将不同的输入重定向至对应的下级智能体。

        参数:
            state_flow: 状态流转规则字典或动态函数，定义成员之间控制流的物理走向。
            selector_func: 极速硬路由的静态选择函数，返回目标智能体名称。
            router: 自定义的动态路由器实例 (如 LLMRouter, RegexRouter 等)。
            leader_model: 路由节点 (Leader) 使用的大模型名称，若为空则默认继承全局。
            leader_tools: 挂载给路由节点 (Leader) 的专属工具列表。
            custom_prompt: 自定义系统提示词，用于覆盖默认的路由系统提示词。
        """
        from zhenxun.services.ai.flow.team.strategy import RouteStrategy

        self.strategy = RouteStrategy(
            state_flow=state_flow,
            selector_func=selector_func,
            router=router,
            leader_model=leader_model,
            leader_tools=leader_tools,
            custom_prompt=custom_prompt,
        )
        self.selector_func = selector_func
        return self

    def with_coordination(
        self,
        leader_model: str | None = None,
        leader_tools: list[ToolSource] | None = None,
        custom_prompt: str | None = None,
    ) -> Self:
        """
        应用协作策略，Leader 自主规划并主动将子任务委派给 Sub-Agents，最后汇总结果。

        协作策略初始化，Leader 主动拆解任务并挂载委托工具，
        委派给 Sub-Agents 并汇总结果。

        参数:
            leader_model: 协调节点 (Leader) 使用的大模型名称，若为空则默认继承全局。
            leader_tools: 挂载给协调节点 (Leader) 的专属附加工具列表。
            custom_prompt: 自定义系统提示词，用于覆盖默认的协调系统提示词。
        """
        from zhenxun.services.ai.flow.team.strategy import CoordinateStrategy

        self.strategy = CoordinateStrategy(
            leader_model=leader_model,
            leader_tools=leader_tools,
            custom_prompt=custom_prompt,
        )
        return self

    def with_broadcast(
        self,
        leader_model: str | None = None,
        leader_tools: list[ToolSource] | None = None,
        custom_prompt: str | None = None,
    ) -> Self:
        """
        应用广播策略，并发让所有成员处理同一个任务，最后由 Leader 总结。

        广播策略初始化，并发让所有成员处理同一个任务，汇总多方报告，最后由 Leader 总结。

        参数:
            leader_model: 总结节点 (Leader) 使用的大模型名称，若为空则默认继承全局。
            leader_tools: 挂载给总结节点 (Leader) 的专属附加工具列表。
            custom_prompt: 自定义系统提示词，用于覆盖默认的广播总结系统提示词。
        """
        from zhenxun.services.ai.flow.team.strategy import BroadcastStrategy

        self.strategy = BroadcastStrategy(
            leader_model=leader_model,
            leader_tools=leader_tools,
            custom_prompt=custom_prompt,
        )
        return self

    def with_task(
        self,
        leader_model: str | None = None,
        leader_tools: list[ToolSource] | None = None,
        max_iterations: int = 15,
        blackboard_schema: type[BaseModel] | None = None,
        initial_blackboard_state: BaseModel | None = None,
        custom_prompt: str | None = None,
    ) -> Self:
        """
        应用任务规划策略，Leader 利用工具箱在黑板上拆解任务、
        管理依赖并驱动 Member 执行。

        任务规划策略初始化，Leader 利用看板在黑板上拆解任务、
        管理依赖并驱动 Member 异步推进。

        参数:
            leader_model: 规划节点 (Leader) 使用的大模型名称，若为空则默认继承全局。
            leader_tools: 挂载给规划节点 (Leader) 的专属附加工具列表。
            max_iterations: 引擎驱动的状态机最大迭代/循环次数，防止死循环。
            blackboard_schema: 团队共享黑板的数据结构类型 (Pydantic Model 类)。
            initial_blackboard_state: 共享黑板的初始数据状态实例。
            custom_prompt: 自定义系统提示词，用于覆盖默认的规划系统提示词。
        """
        from zhenxun.services.ai.flow.team.strategy import TaskStrategy

        self.strategy = TaskStrategy(
            leader_model=leader_model,
            leader_tools=leader_tools,
            max_iterations=max_iterations,
            blackboard_schema=blackboard_schema,
            initial_blackboard_state=initial_blackboard_state,
            custom_prompt=custom_prompt,
        )
        return self

    def _ensure_strategy(self):
        if self.strategy is None:
            raise RuntimeError(
                f"Team '{self.name}' 尚未绑定任何协作策略！"
                "请先调用 .with_routing() 等链式方法进行配置，"
                "或在初始化时传入 strategy 参数。"
            )

    async def run(
        self,
        prompt: PromptInput | Task | None = None,
        *,
        context: "RunContext | None" = None,
        capabilities: list[CapabilitySource] | None = None,
        skills: Sequence[str | Path | Skill | SkillSource] | None = None,
        **kwargs: Any,
    ) -> AgentRunResult[Any]:
        """
        团队级运行阻塞核心入口，内部静默分配任务给成员直至汇总结束。

        参数:
            prompt: 派发给多智能体团队的任务描述 or 契约对象 (Task)。
            context: 显式传入的会话与运行上下文。
            capabilities: 仅针对本次团队执行动态注入的临时拦截器列表。
            kwargs: 透传的其他附加参数。

        返回:
            AgentRunResult[Any]: 包含最终融合输出、消息历史和用量统计的运行结果对象。
        """
        self._ensure_strategy()
        if skills:
            from zhenxun.services.ai.tools.providers.skills.capabilities import (
                SkillCapability,
            )

            capabilities = list(capabilities) if capabilities else []
            capabilities.append(
                SkillCapability(skills=skills, namespace=self.namespace)
            )

        return await super().run(
            prompt=prompt, context=context, capabilities=capabilities, **kwargs
        )

    import contextlib

    @contextlib.asynccontextmanager
    async def run_stream(
        self,
        prompt: PromptInput | Task | None = None,
        *,
        context: "RunContext | None" = None,
        capabilities: list[CapabilitySource] | None = None,
        skills: Sequence[str | Path | Skill | SkillSource] | None = None,
        **kwargs: Any,
    ):
        self._ensure_strategy()

        if context is None:
            context = RunContext()

        if not hasattr(context, "capabilities"):
            context.capabilities = []

        if hasattr(self, "capabilities") and self.capabilities:
            context.capabilities.extend(self.capabilities)

        if skills:
            from zhenxun.services.ai.tools.providers.skills.capabilities import (
                SkillCapability,
            )

            capabilities = list(capabilities) if capabilities else []
            capabilities.append(
                SkillCapability(skills=skills, namespace=self.namespace)
            )

        if capabilities:
            for cap in capabilities:
                if isinstance(cap, AbstractCapability):
                    context.capabilities.append(cap)
                elif callable(cap):
                    context.capabilities.append(DynamicCapability(cap))

        from zhenxun.services.ai.flow.team.runner import TeamRunner
        from zhenxun.services.ai.run import StreamedRunResult

        event_bus = EventBus()
        context.run.event_bus = event_bus
        assert self.strategy is not None
        runner = TeamRunner(self, self.strategy)

        policy = getattr(self.runtime_config, "concurrency_policy", None)
        if policy is None:
            from zhenxun.services.ai.flow.base import ConcurrencyPolicy

            policy = (
                ConcurrencyPolicy.ALLOW
                if getattr(self.runtime_config, "stateless", True)
                else ConcurrencyPolicy.QUEUE
            )

        intervention_policy = getattr(self.runtime_config, "intervention_policy", None)

        from zhenxun.services.ai.utils import ContextUtils

        lock_id = ContextUtils.extract_concurrency_lock_id(
            context,
            getattr(self.runtime_config, "concurrency_scope", None),
            context.session_id or "default_session",
        )

        async def _execution_task():
            from zhenxun.services.ai.flow.concurrency import apply_concurrency_policy

            cancel_token = context.run.cancellation_token or CancellationToken()
            context.run.cancellation_token = cancel_token

            try:
                async with apply_concurrency_policy(
                    session_id=context.session_id or "default_session",
                    lock_id=lock_id,
                    policy=policy,
                    cancel_token=cancel_token,
                    intervention_policy=intervention_policy,
                    message=prompt,
                ):
                    async for event in runner.run_stream(prompt, context, **kwargs):
                        await event_bus.emit(event)
            except BaseException as e:
                from zhenxun.services.ai.run.models import AgentRunError

                if isinstance(e, asyncio.CancelledError):
                    e = ConcurrencyInterruptException("团队执行已被新请求打断并接管")
                await event_bus.emit(AgentRunError(error=e))
            finally:
                await event_bus.end()

        task = asyncio.create_task(_execution_task())
        result_obj = StreamedRunResult[Any](event_bus)

        try:
            yield result_obj
        finally:
            if not task.done():
                task.cancel()

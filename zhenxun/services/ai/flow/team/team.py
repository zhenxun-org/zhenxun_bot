from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from typing_extensions import Self

from pydantic import BaseModel

from zhenxun.services.ai.capabilities import (
    AbstractCapability,
    CapabilitySource,
    DynamicCapability,
)
from zhenxun.services.ai.core.messages import PromptInput
from zhenxun.services.ai.core.models import CancellationToken
from zhenxun.services.ai.core.stream_events import AgentStreamEvent, EventBus
from zhenxun.services.ai.flow.agent.agent import ToolSource
from zhenxun.services.ai.flow.agent.models import Persona
from zhenxun.services.ai.flow.core.base import BaseRunnable
from zhenxun.services.ai.run import (
    AgentRunResult,
    AgentTask,
    RunContext,
    RunIntent,
)
from zhenxun.services.ai.tools.providers.skills.capabilities import SkillCapability
from zhenxun.services.ai.tools.providers.skills.models import Skill, SkillSource
from zhenxun.utils.utils import infer_plugin_namespace

from .models import TeamRuntimeConfig, Transition
from .router import BaseRouter
from .strategy import (
    BaseTeamStrategy,
)


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
        persona: Persona | None = None,
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
        self.persona = persona

        self.namespace = infer_plugin_namespace() or "unknown"

        if description:
            self.description = description
        else:
            self.description = f"一个名为 {self.name} 的协作团队，"
            f"包含 {len(self.members)} 个处理节点。"

        self.capabilities: list[AbstractCapability] = []
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

    @property
    def default_model(self) -> str | None:
        """获取当前团队默认调用的可用大模型。优先取自身配置，其次遍历成员寻找可用模型。"""
        if getattr(self, "model", None):
            return str(self.model() if callable(self.model) else self.model)

        for m in self.members:
            m_model = getattr(m, "model_name", None) or getattr(m, "model", None)
            if m_model:
                return str(m_model() if callable(m_model) else m_model)
        return None

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
        state_flow: (Mapping[str, Sequence[Transition | str]] | Callable | None) = None,
        selector_func: Callable[..., str | None] | None = None,
        router: BaseRouter | None = None,
        leader_model: str | None = None,
        leader_tools: list[ToolSource] | None = None,
        custom_prompt: str | None = None,
        max_handoffs: int = 3,
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
            max_handoffs: 同一会话中允许连续移交的最大次数，防止无限踢皮球。
        """
        from .strategy import RouteStrategy

        self.strategy = RouteStrategy(
            state_flow=state_flow,
            selector_func=selector_func,
            router=router,
            leader_model=leader_model,
            leader_tools=leader_tools,
            custom_prompt=custom_prompt,
            max_handoffs=max_handoffs,
        )
        self.selector_func = selector_func
        return self

    def with_coordination(
        self,
        leader_model: str | None = None,
        leader_tools: list[ToolSource] | None = None,
        custom_prompt: str | None = None,
        max_delegations: int = 3,
    ) -> Self:
        """
        应用协作策略，Leader 自主规划并主动将子任务委派给 Sub-Agents，最后汇总结果。

        协作策略初始化，Leader 主动拆解任务并挂载委托工具，
        委派给 Sub-Agents 并汇总结果。

        参数:
            leader_model: 协调节点 (Leader) 使用的大模型名称，若为空则默认继承全局。
            leader_tools: 挂载给协调节点 (Leader) 的专属附加工具列表。
            custom_prompt: 自定义系统提示词，用于覆盖默认的协调系统提示词。
            max_delegations: 允许向同一个专员连续委派失败的最大重试次数。
        """
        from .strategy import CoordinateStrategy

        self.strategy = CoordinateStrategy(
            leader_model=leader_model,
            leader_tools=leader_tools,
            custom_prompt=custom_prompt,
            max_delegations=max_delegations,
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
        from .strategy import BroadcastStrategy

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
        blackboard: type[BaseModel] | BaseModel | None = None,
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
            blackboard: (可选) 团队共享黑板。可传入 Schema 类型类，或直接传入带有初始数据的 Schema 实例对象。
            custom_prompt: 自定义系统提示词，用于覆盖默认的规划系统提示词。
        """  # noqa: E501
        from .strategy import TaskStrategy

        self.strategy = TaskStrategy(
            leader_model=leader_model,
            leader_tools=leader_tools,
            max_iterations=max_iterations,
            blackboard=blackboard,
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
        prompt: PromptInput | AgentTask | None = None,
        *,
        context: "RunContext | None" = None,
        capabilities: list[CapabilitySource] | None = None,
        skills: Sequence[str | Path | Skill | SkillSource] | None = None,
        **kwargs: Any,
    ) -> AgentRunResult[Any]:
        """
        团队级运行阻塞核心入口，内部静默分配任务给成员直至汇总结束。

        参数:
            prompt: 派发给多智能体团队的任务描述 or 契约对象 (AgentTask)。
            context: 显式传入的会话与运行上下文。
            capabilities: 仅针对本次团队执行动态注入的临时拦截器列表。
            kwargs: 透传的其他附加参数。

        返回:
            AgentRunResult[Any]: 包含最终融合输出、消息历史和用量统计的运行结果对象。
        """
        self._ensure_strategy()
        if skills:
            capabilities = list(capabilities) if capabilities else []
            capabilities.append(
                SkillCapability(skills=skills, namespace=self.namespace)
            )

        return await super().run(
            prompt=prompt, context=context, capabilities=capabilities, **kwargs
        )

    async def _execute_stream(
        self,
        intent: RunIntent,
        context: RunContext,
        cancel_token: CancellationToken,
        event_bus: EventBus,
        **kwargs: Any,
    ) -> AsyncIterator[AgentStreamEvent]:
        self._ensure_strategy()

        capabilities = kwargs.pop("capabilities", None)
        skills = kwargs.pop("skills", None)

        if not hasattr(context, "capabilities"):
            context.capabilities = []

        if hasattr(self, "capabilities") and self.capabilities:
            context.capabilities.extend(self.capabilities)

        if skills:
            capabilities = list(capabilities) if capabilities else []
            from zhenxun.services.ai.tools.providers.skills.capabilities import (
                SkillCapability,
            )

            capabilities.append(
                SkillCapability(skills=skills, namespace=self.namespace)
            )

        if capabilities:
            for cap in capabilities:
                if isinstance(cap, AbstractCapability):
                    context.capabilities.append(cap)
                elif callable(cap):
                    context.capabilities.append(DynamicCapability(cap))

        from .runner import TeamRunner

        assert self.strategy is not None
        runner = TeamRunner(self, self.strategy)

        async for event in runner.run_stream(intent, context, **kwargs):
            yield event

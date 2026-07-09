from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping, Sequence
import inspect
import re
from typing import Any, cast

from nonebot.utils import is_coroutine_callable

from zhenxun.services.ai.core.messages import AgentMessage
from zhenxun.services.ai.core.templates import PromptTemplate
from zhenxun.services.ai.run import AgentTask, RunContext
from zhenxun.services.ai.run.di import DependencyInjector
from zhenxun.services.ai.utils.logger import log_team as logger

from .models import RouteDecision, Transition


class BaseRouter(ABC):
    """团队多智能体路由器基类"""

    @abstractmethod
    async def route(
        self,
        context: RunContext,
        history: Sequence[AgentMessage],
        prompt: str | AgentTask | None = None,
    ) -> RouteDecision | None:
        """核心路由方法"""
        pass


class FunctionRouter(BaseRouter):
    """基于纯函数的极速路由器"""

    def __init__(self, selector_func: Callable[..., Any], target: str | None = None):
        """
        初始化基于函数的极速路由器。

        参数:
            selector_func: 用于进行路由判断的选择函数，返回布尔值或字符串目标名。
            target: 当选择函数返回 True 时，默认路由到的目标成员名称。
        """
        self.selector_func = selector_func
        self.target = target

    async def route(
        self,
        context: RunContext,
        history: Sequence[AgentMessage],
        prompt: str | AgentTask | None = None,
    ) -> RouteDecision | None:
        sig = inspect.signature(self.selector_func)
        call_kwargs = {"prompt": prompt, "context": context, "history": history}
        if isinstance(prompt, AgentTask):
            call_kwargs["agent_task"] = prompt
            call_kwargs["task"] = prompt

        kwargs_resolved = await DependencyInjector.resolve_all(
            sig, call_kwargs, context
        )
        filtered_kwargs = {
            k: v for k, v in kwargs_resolved.items() if k in sig.parameters
        }

        if is_coroutine_callable(self.selector_func):
            _async_func = cast(Callable[..., Awaitable[Any]], self.selector_func)
            selected_target = await _async_func(**filtered_kwargs)
        else:
            _sync_func = cast(Callable[..., Any], self.selector_func)
            selected_target = _sync_func(**filtered_kwargs)

        if isinstance(selected_target, bool):
            if selected_target and self.target:
                logger.debug(f"命中函数极速路由 -> {self.target}")
                return RouteDecision(target_name=self.target, reason="")
        elif selected_target is not None and isinstance(selected_target, str):
            logger.debug(f"命中函数动态路由 -> {selected_target}")
            return RouteDecision(target_name=selected_target, reason="")
        return None


class RegexRouter(BaseRouter):
    """基于正则表达式的极速路由器"""

    def __init__(self, pattern: str, target: str):
        """
        初始化基于正则表达式的极速路由器。

        参数:
            pattern: 正则表达式匹配规则。
            target: 当正则表达式成功匹配用户输入时路由到的目标成员名称。
        """
        self.pattern = re.compile(pattern)
        self.target = target

    async def route(
        self,
        context: RunContext,
        history: Sequence[AgentMessage],
        prompt: str | AgentTask | None = None,
    ) -> RouteDecision | None:
        text_to_match = (
            prompt.description
            if isinstance(prompt, AgentTask)
            else (prompt or context.run.user_input or "")
        )

        if self.pattern.search(text_to_match):
            logger.debug(f"命中正则极速路由 -> {self.target}")
            return RouteDecision(target_name=self.target, reason="")
        return None


class ChainRouter(BaseRouter):
    """责任链路由器：按顺序执行，直到其中一个命中"""

    def __init__(self, routers: list[BaseRouter]):
        """
        初始化责任链路由器。

        参数:
            routers: 路由器实例列表，按顺序链式匹配，遇到首个命中的路由器即返回。
        """
        self.routers = routers

    async def route(
        self,
        context: RunContext,
        history: Sequence[AgentMessage],
        prompt: str | AgentTask | None = None,
    ) -> RouteDecision | None:
        for router in self.routers:
            decision = await router.route(context, history, prompt)
            if decision is not None:
                return decision
        return None


class LLMRouter(BaseRouter):
    """基于大模型的意图路由器"""

    def __init__(
        self,
        team_name: str,
        members: list[Any],
        leader_model: str | None = None,
        leader_tools: list[Any] | None = None,
        state_flow: Mapping[str, Sequence[Transition | str]] | Callable | None = None,
        runtime_config: Any = None,
        custom_prompt: str | None = None,
        allowed_transitions: list[Transition] | None = None,
        max_handoffs: int = 3,
    ):
        """
        初始化基于大模型的意图路由器。

        参数:
            team_name: 当前团队的名称标识。
            members: 团队的成员列表，包含 Agent, Team 或 Workflow。
            leader_model: 用于进行意图决策的路由器大模型名称，若为空则默认继承全局配置。
            leader_tools: 挂载给意图决策路由器的额外可用工具列表。
            state_flow: 状态流转规则字典或动态流转函数，定义智能体成员之间的转接路径。
            runtime_config: 团队级别的运行时全局配置。
            custom_prompt: 自定义的系统提示词模板，用以覆盖默认的路由系统指令。
            allowed_transitions: 允许的状态移交规则与前置条件列表。
            max_handoffs: 同一会话中允许连续移交的最大次数。
        """
        self.team_name = team_name
        self.members = members
        self.leader_model = leader_model
        self.leader_tools = leader_tools or []
        self.state_flow = state_flow
        self.runtime_config = runtime_config
        self.custom_prompt = custom_prompt
        self.allowed_transitions = allowed_transitions
        self.max_handoffs = max_handoffs

    async def route(
        self,
        context: RunContext,
        history: Sequence[AgentMessage],
        prompt: str | AgentTask | None = None,
    ) -> RouteDecision | None:
        from zhenxun.services.ai.flow.agent.agent import Agent
        from zhenxun.services.ai.flow.agent.models import AgentConfig

        from .capabilities import TeamRoutingCapability

        default_system_prompt = """## 角色与目标
你是一个高级任务路由器 (所在团队: {{ team_name }})。
请根据用户的输入意图，立刻调用相应的移交工具 (transfer_to_...)
将对话物理转移给合适的专员处理。
你必须且只能选择移交，不能自己作答。"""

        if self.allowed_transitions:
            transitions_desc = "\n## 可用的移交目标及条件：\n"
            for t in self.allowed_transitions:
                desc = getattr(t, "description", "") or "无特定条件"
                transitions_desc += (
                    f"- 移交至 [{getattr(t, 'target', 'unknown')}]：{desc}\n"
                )
            default_system_prompt += transitions_desc

        template = self.custom_prompt or default_system_prompt
        route_prompt = PromptTemplate(template).render(team_name=self.team_name)

        routing_cap = TeamRoutingCapability(
            team_name=self.team_name,
            members=self.members,
            state_flow=self.state_flow,
            max_handoffs=self.max_handoffs,
        )

        leader_config = AgentConfig(
            stateless=self.runtime_config.stateless if self.runtime_config else True,
            enable_hitl=getattr(self.runtime_config, "leader_enable_hitl", False),
        )

        target_model = self.leader_model
        if not target_model:
            for m in self.members:
                if m_model := getattr(m, "model_name", None) or getattr(
                    m, "model", None
                ):
                    target_model = m_model
                    break

        router_agent = Agent(
            name=f"{self.team_name}_Router",
            instruction=route_prompt,
            model=target_model,
            tools=self.leader_tools,
            config=leader_config,
        )

        sub_context = context.clone_for_member(router_agent.name)
        sub_context.capabilities = list(sub_context.capabilities)
        sub_context.capabilities.append(routing_cap)

        logger.debug("🤖 [LLMRouter] 启动 LLM 思考路由决策...")

        res = await router_agent.run(
            prompt=prompt,
            context=sub_context,
            config=AgentConfig(message_history=history),
        )
        if res.handoff:
            logger.debug(f"🤖 [LLMRouter] 决策完毕: 移交给 -> {res.handoff.target}")
            return RouteDecision(
                target_name=res.handoff.target,
                reason=res.handoff.reason,
                context_data=res.handoff.context_data,
            )

        logger.warning("🤖 [LLMRouter] LLM 没有调用移交工具，放弃路由。")
        return None

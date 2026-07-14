from collections.abc import Callable, Mapping, Sequence

from zhenxun.services.ai.capabilities import AbstractCapability
from zhenxun.services.ai.flow.core.base import BaseRunnable
from zhenxun.services.ai.run import RunContext
from zhenxun.services.ai.run.di import DependencyInjector
from zhenxun.services.ai.tools.bridges.handoff import HandoffTool
from zhenxun.services.ai.tools.core.tool import BaseTool

from .models import Transition


class TeamRoutingCapability(AbstractCapability):
    """团队路由能力组件：动态向所有团队成员"""

    def __init__(
        self,
        team_name: str,
        members: list[BaseRunnable],
        state_flow: Mapping[str, Sequence[Transition | str]] | Callable | None = None,
        max_handoffs: int = 3,
    ):
        self.team_name = team_name
        self.members = members
        self.state_flow = state_flow
        self.max_handoffs = max_handoffs

    async def _get_allowed_transitions(
        self, context: RunContext
    ) -> list[Transition] | None:
        """核心FSM解析：解析静态字典或动态执行函数获取允许的 Transition 列表"""
        if self.state_flow is None:
            return None

        current_speaker = context.run.agent_name or "unknown"

        if isinstance(self.state_flow, dict):
            raw_targets = self.state_flow.get(
                current_speaker,
                [m.name for m in self.members if m.name != current_speaker],
            )

            return [
                Transition(target=t) if isinstance(t, str) else t for t in raw_targets
            ]

        if callable(self.state_flow):
            result = await DependencyInjector.invoke(
                self.state_flow, call_kwargs={}, context=context
            )

            if result is None:
                return None

            return [Transition(target=t) if isinstance(t, str) else t for t in result]

        return None

    async def get_tools(self, context: RunContext) -> list[BaseTool]:
        tools = []
        allowed_transitions = await self._get_allowed_transitions(context)

        for m in self.members:
            if context.run.agent_name != m.name:
                transition = None
                if allowed_transitions is not None:
                    transition = next(
                        (
                            t
                            for t in allowed_transitions
                            if getattr(t, "target", "") == m.name
                        ),
                        None,
                    )
                    if transition is None:
                        continue

                desc = m.profile_summary

                if transition and getattr(transition, "description", ""):
                    desc += f" 【移交条件】：{transition.description}"

                input_schema = (
                    getattr(transition, "input_schema", None) if transition else None
                )

                tools.append(
                    HandoffTool(
                        target_name=m.name,
                        target_description=desc,
                        input_schema=input_schema,
                        max_handoffs=self.max_handoffs,
                    )
                )
        return tools

    async def get_system_prompts(self, context: RunContext) -> list[str]:
        if context.run.agent_name != f"{self.team_name}_Router":
            base_prompt = f"""### 🤝 [团队协作规范]
你是跨域协作团队 '{self.team_name}' 的一员。如果你认为当前任务超出了你的职责范畴，
或你目前已经完成了前置处理但需要其他专家的处理结果进行下一步推进，
请务必使用移交工具 (transfer_to_...) 将控制权移交给合适的队友。
移交时必须在 `reason` 参数中详细说明你的移交原因，
并附带你已经处理好的上下文关键数据！"""

            allowed_transitions = await self._get_allowed_transitions(context)
            if allowed_transitions is not None:
                if not allowed_transitions:
                    base_prompt += """

⚠️ **[系统状态机规则] 当前流程已到达终点！你没有任何可移交的对象。
请直接输出最终总结并结束当前任务，严禁尝试移交。**"""
                else:
                    targets = [
                        getattr(t, "target", "unknown") for t in allowed_transitions
                    ]
                    base_prompt += f"""

⚠️ **[系统状态机规则] 根据当前的状态流转限制，如果你需要移交控制权，
你必须且只能从以下对象中选择：
[{", ".join(targets)}]。禁止移交给除此之外的任何实体！**"""

            return [base_prompt]
        return []

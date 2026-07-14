from abc import ABC
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
import json
import re
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

from zhenxun.services.ai.capabilities import AbstractCapability
from zhenxun.services.ai.core.exceptions import AbortException
from zhenxun.services.ai.core.messages import AgentMessage, LLMMessage
from zhenxun.services.ai.core.stream_events import ToolStreamChunkEvent
from zhenxun.services.ai.core.templates import PromptTemplate
from zhenxun.services.ai.flow.agent.agent import Agent, ToolSource
from zhenxun.services.ai.flow.agent.models import AgentConfig
from zhenxun.services.ai.flow.core.base import BaseRunnable
from zhenxun.services.ai.run import RunContext, RunIntent
from zhenxun.services.ai.run.blackboard import BlackboardManager
from zhenxun.services.ai.tools.bridges.delegate import DelegateTool
from zhenxun.services.ai.tools.providers.builtin.blackboard import BlackboardToolkit
from zhenxun.services.ai.utils.logger import log_team as logger

from .models import (
    CallAction,
    ConcurrentCallAction,
    FinishAction,
    TaskBoardState,
    TaskNodeStatus,
    TeamAction,
    Transition,
)
from .router import BaseRouter, ChainRouter, FunctionRouter, LLMRouter
from .task_tools import TaskPlanningToolkit

if TYPE_CHECKING:
    from .team import Team


class BaseTeamStrategy(ABC):
    """多智能体团队协作策略基类"""

    default_system_prompt: str = ""

    def __init__(self, custom_prompt: str | None = None):
        """
        多智能体团队协作策略基类初始化。

        参数:
            custom_prompt: 自定义系统提示词，用于覆盖默认的团队系统提示词模板。
        """
        self.custom_prompt = custom_prompt

    def get_prompt(self, **kwargs) -> str:
        template = self.custom_prompt or self.default_system_prompt
        return PromptTemplate(template).render(**kwargs)

    def get_member_capabilities(
        self, team: "Team", member: BaseRunnable
    ) -> list[AbstractCapability]:
        """获取派发给子成员时需要动态注入的能力组件"""
        return []

    async def generate_plan(
        self,
        team: "Team",
        intent: RunIntent,
        context: RunContext,
        **kwargs,
    ) -> AsyncGenerator[TeamAction, Any]:
        """
        核心决策生成器 (Action Yielding Pattern)。

        第三方开发者只需重写此方法：
        1. 使用 `yield CallAction(...)` 派发任务，系统会自动拦截并执行，
           然后将 `AgentRunResult` 通过 .asend() 传回。
        2. 使用 `yield FinishAction(...)` 结束团队协作。

        """
        yield FinishAction(result="该策略尚未实现 generate_plan() 方法。")

    def _build_leader_agent(
        self,
        team: "Team",
        role_name: str,
        extra_instruction: str = "",
        extra_tools: list[ToolSource] | None = None,
    ) -> Agent:
        """
        统一的团队 Leader / Planner / Broadcaster 装配工厂。
        自动合并默认指令、追加指令、基类工具和策略专属工具，
        并处理无状态配置以及 HITL 状态继承。
        """
        instruction = self.get_prompt()
        if extra_instruction:
            instruction += f"\n\n{extra_instruction}"

        tools = getattr(self, "leader_tools", []).copy()
        if extra_tools:
            tools.extend(extra_tools)

        leader_config = AgentConfig(
            stateless=team.runtime_config.stateless if team.runtime_config else True,
            enable_hitl=getattr(team.runtime_config, "leader_enable_hitl", False),
        )

        target_model = getattr(self, "leader_model", None) or team.default_model

        return Agent(
            name=f"{team.name}_{role_name}",
            instruction=instruction,
            persona=team.persona,
            model=target_model,
            tools=tools,
            config=leader_config,
        )


class RouteStrategy(BaseTeamStrategy):
    """路由策略：基于挂载的 Router 进行最合适的专家分发"""

    def __init__(
        self,
        state_flow: Mapping[str, Sequence[str | Any]] | Callable | None = None,
        selector_func: Callable[..., str | None] | None = None,
        router: BaseRouter | None = None,
        leader_model: str | None = None,
        leader_tools: list[ToolSource] | None = None,
        custom_prompt: str | None = None,
        max_handoffs: int = 3,
    ):
        """
        路由策略初始化，基于挂载的 Router 进行最合适的专家分发。

        参数:
            state_flow: 状态流转规则字典或动态函数，定义成员之间控制流的物理走向。
            selector_func: 极速硬路由的静态选择函数，返回目标智能体名称。
            router: 自定义的动态路由器实例 (如 LLMRouter, RegexRouter 等)。
            leader_model: 路由节点 (Leader) 使用的大模型名称，若为空则默认继承全局。
            leader_tools: 挂载给路由节点 (Leader) 的专属工具列表。
            custom_prompt: 自定义系统提示词，用于覆盖默认的路由系统提示词。
            max_handoffs: 同一会话中允许连续移交的最大次数。
        """
        super().__init__(custom_prompt=custom_prompt)
        self.selector_func = selector_func
        self.router = router
        self.leader_model = leader_model
        self.leader_tools = leader_tools or []
        self.max_handoffs = max_handoffs

        if isinstance(state_flow, dict):
            normalized_flow = {}
            for k, targets in state_flow.items():
                normalized_targets = []
                for t in targets:
                    if isinstance(t, str):
                        normalized_targets.append(Transition(target=t))
                    else:
                        normalized_targets.append(t)
                normalized_flow[k] = normalized_targets
            self.state_flow = normalized_flow
        else:
            self.state_flow = state_flow

    def get_member_capabilities(
        self, team: "Team", member: BaseRunnable
    ) -> list[AbstractCapability]:
        from .capabilities import TeamRoutingCapability

        return [
            TeamRoutingCapability(
                team_name=team.name,
                members=team.members,
                state_flow=self.state_flow,
                max_handoffs=self.max_handoffs,
            )
        ]

    def _build_handoff_history(
        self, handoff_reason: str, context_data: Any
    ) -> list[AgentMessage]:
        """将移交数据格式化为系统的引导历史消息"""
        handoff_history_messages = []
        upstream_info = []
        if handoff_reason:
            upstream_info.append(f"【移交说明】\n{handoff_reason}")
        if context_data:
            if isinstance(context_data, dict):
                formatted_data = json.dumps(context_data, ensure_ascii=False, indent=2)
                upstream_info.append(
                    f"【结构化上下文载荷】\n```json\n{formatted_data}\n```"
                )
            else:
                upstream_info.append(f"【核心上下文数据】\n{context_data}")

        if combined_info := "\n\n".join(upstream_info):
            handoff_msg = LLMMessage.system(
                f"### 🔄 [来自上游节点的移交数据]\n{combined_info}"
            )
            handoff_history_messages.append(handoff_msg)
        return handoff_history_messages

    def _check_fast_route(
        self, current_target: str, output_str: str
    ) -> tuple[bool, str]:
        """检查并执行快速硬路由，返回(是否触发路由, 新目标名称)"""
        if (
            not isinstance(self.state_flow, dict)
            or current_target not in self.state_flow
        ):
            return False, current_target

        for t in self.state_flow[current_target]:
            if trigger_regex := getattr(t, "trigger_regex", None):
                if re.search(trigger_regex, output_str):
                    return True, getattr(t, "target", current_target)

            if trigger_func := getattr(t, "trigger_func", None):
                try:
                    res = trigger_func(output_str)
                    if res:
                        if isinstance(res, str):
                            return True, res
                        return True, getattr(t, "target", current_target)
                except Exception:
                    pass
        return False, current_target

    async def generate_plan(
        self,
        team: "Team",
        intent: RunIntent,
        context: RunContext,
        **kwargs,
    ) -> AsyncGenerator[TeamAction, Any]:
        router = self.router
        if not router:
            routers = []
            if self.selector_func:
                routers.append(FunctionRouter(self.selector_func))
            routers.append(
                LLMRouter(
                    team_name=team.name,
                    members=team.members,
                    leader_model=self.leader_model or team.default_model,
                    leader_tools=self.leader_tools,
                    state_flow=self.state_flow,
                    runtime_config=getattr(team, "runtime_config", None),
                    custom_prompt=self.custom_prompt,
                    max_handoffs=self.max_handoffs,
                )
            )
            router = ChainRouter(routers)

        cycle_count = 0
        exec_config = kwargs.get("config")
        max_cycles = getattr(exec_config, "max_cycles", 15) if exec_config else 15

        logger.debug(f"🛣️ '{team.name}' 正在获取初始路由决策...")

        decision = await router.route(context, [], intent)
        if not decision:
            logger.warning(f"🚨 Team '{team.name}' 的所有路由策略未能命中目标。")
            raise AbortException(
                reason=f"Team '{team.name}' 无法找到合适的路由节点处理该任务",
                display="🚨 团队协作失败，无法分配任务。",
            )

        current_target = decision.target_name
        handoff_reason = decision.reason
        context_data = decision.context_data

        while True:
            cycle_count += 1
            if cycle_count > max_cycles:
                logger.error(
                    f"🚨 Team '{team.name}' 路由陷入死循环！"
                    f"已达到最大限制 {max_cycles} 次。"
                )
                raise AbortException(
                    reason=(
                        f"Team '{team.name}' 路由流转超过最大次数限制"
                        f" ({max_cycles}次)，"
                        "已强制熔断。"
                    ),
                    display="🚨 团队协作陷入死循环，已被系统强制中断。",
                )

            handoff_history_messages = self._build_handoff_history(
                handoff_reason, context_data
            )

            run_result = yield CallAction(
                agent=current_target,
                task=intent.original_input or "",
                history=handoff_history_messages,
                kwargs=kwargs,
            )

            if run_result.handoff:
                current_target = run_result.handoff.target
                handoff_reason = run_result.handoff.reason
                context_data = run_result.handoff.context_data
                continue

            output_str = str(run_result.output)

            fast_routed, new_target = self._check_fast_route(current_target, output_str)
            if fast_routed:
                current_target = new_target
                handoff_reason = ""
                context_data = output_str
                logger.debug(
                    f"🛣️ **路由决策**: 委派给专员 👨💼`{current_target}`"
                    "(系统拦截：正则/函数状态流发生转移)"
                )
                continue

            yield FinishAction(result=run_result)
            break


class CoordinateStrategy(BaseTeamStrategy):
    """协作策略：Leader 自主规划，委派任务给 Sub-Agents 并汇总结果"""

    default_system_prompt = """## 角色与目标
你是一个多智能体团队的协调者（Leader）。
你可以使用你自身携带的工具先查阅、收集资料；也可以分析用户的目标将其拆解为子任务，并委派给合适的下属专员。
当你收集齐所有需要的信息或专员报告后，请汇总生成最终回复向用户汇报。"""

    def __init__(
        self,
        leader_model: str | None = None,
        leader_tools: list[ToolSource] | None = None,
        custom_prompt: str | None = None,
        max_delegations: int = 3,
    ):
        """
        协作策略初始化，Leader 主动拆解任务，委派给 Sub-Agents 并汇总结果。

        参数:
            leader_model: 协调节点 (Leader) 使用的大模型名称，若为空则默认继承全局。
            leader_tools: 挂载给协调节点 (Leader) 的专属工具列表.
            custom_prompt: 自定义系统提示词，用于覆盖默认的协调系统提示词。
            max_delegations: 允许向同一个专员连续委派失败的最大重试次数。
        """
        super().__init__(custom_prompt=custom_prompt)
        self.leader_model = leader_model
        self.leader_tools = leader_tools or []
        self.max_delegations = max_delegations

    async def generate_plan(
        self,
        team: "Team",
        intent: RunIntent,
        context: RunContext,
        **kwargs,
    ) -> AsyncGenerator[TeamAction, Any]:
        delegation_tools = []
        for m in team.members:
            desc = m.profile_summary

            delegation_tools.append(
                DelegateTool(
                    runnable=m,
                    name=f"delegate_to_{m.name}",
                    description=f"将子任务委派给专员 [{m.name}] 处理。专长：{desc}",
                    max_delegations=self.max_delegations,
                )
            )

        leader_agent = self._build_leader_agent(
            team=team,
            role_name="Leader",
            extra_tools=delegation_tools,
        )

        logger.debug(f"✨ **团队 [{team.name}] Leader** 正在汇总各方报告...")

        await context.run.emit(
            ToolStreamChunkEvent(
                tool_name="Team Leader",
                content="✨ 团队 Leader 正在汇总各方报告...",
            )
        )

        logger.debug(f"👨💼 [CoordinateStrategy] '{team.name}' 正在启动协调推理循环...")

        leader_res = yield CallAction(
            agent=leader_agent, task=intent.original_input or ""
        )

        yield FinishAction(result=leader_res)


class BroadcastStrategy(BaseTeamStrategy):
    """广播策略：并发让所有成员处理同一个任务，最后由 Leader 总结"""

    default_system_prompt = """## 角色与目标
你是一个多智能体团队的总结者（Leader）。
以下是各位专家的独立处理结果，请融合各方观点，取长补短，给出一份最终的总结报告。"""

    def __init__(
        self,
        leader_model: str | None = None,
        leader_tools: list[ToolSource] | None = None,
        custom_prompt: str | None = None,
    ):
        """
        广播策略初始化，并发让所有成员处理同一个任务，最后由 Leader 总结。

        参数:
            leader_model: 总结节点 (Leader) 使用的大模型名称，若为空则默认继承全局。
            leader_tools: 挂载给总结节点 (Leader) 的专属工具列表。
            custom_prompt: 自定义系统提示词，用于覆盖默认的广播总结系统提示词。
        """
        super().__init__(custom_prompt=custom_prompt)
        self.leader_model = leader_model
        self.leader_tools = leader_tools or []

    async def generate_plan(
        self,
        team: "Team",
        intent: RunIntent,
        context: RunContext,
        **kwargs,
    ) -> AsyncGenerator[TeamAction, Any]:
        task_desc_str = intent.text

        await context.run.emit(
            ToolStreamChunkEvent(
                tool_name="Team Broadcaster",
                content=f"🚀 正在并发广播任务给 {len(team.members)} 位专家...",
            )
        )

        actions = [
            CallAction(agent=m.name, task=intent.original_input or "")
            for m in team.members
        ]
        results = yield ConcurrentCallAction(actions=actions)

        await context.run.emit(
            ToolStreamChunkEvent(
                tool_name="Team Leader",
                content="✨ 所有专家汇报完毕，Leader 正在融合各方观点...",
            )
        )

        logger.debug(f"✨ **团队 [{team.name}] Leader** 正在汇总各方报告...")

        summary_text = "\n\n".join(
            [f"### 【{name} 的意见】:\n{res.output}" for name, res in results]
        )

        synthesize_prompt = (
            f"**用户原始任务**: {task_desc_str}\n\n"
            f"以下是各位专家的独立处理结果，请融合各方观点，给出一份最终的总结报告：\n\n"
            f"{summary_text}"
        )

        leader_agent = self._build_leader_agent(
            team=team,
            role_name="Leader",
        )

        leader_res = yield CallAction(agent=leader_agent, task=synthesize_prompt)

        yield FinishAction(result=leader_res)


class TaskStrategy(BaseTeamStrategy):
    """任务规划策略：Leader 利用工具箱在黑板上拆解任务、管理依赖并驱动 Member 执行"""

    default_system_prompt = """<how_to_respond>
你是一个多智能体团队的项目经理（Planner）。
请仔细阅读用户的请求，将其拆解为一个个具体的子任务，
并利用 `create_task` 建立所有任务和依赖关系（注意 `assignee` 必须严格从下方的团队成员中选择）。
【⚠️核心执行流规范】
1. 分配完毕后，**必须立刻停止调用任何工具，并直接输出纯文本回复**
（如：'任务已分配，等待执行'），从而结束你的当前回合。
2. 当底层自动执行完毕后，系统会再次唤醒你并提供最新的看板结果。
请根据结果决定是下发新任务、要求重做，还是调用 `mark_all_complete` 汇报总结。
</how_to_respond>"""  # noqa: E501

    def __init__(
        self,
        leader_model: str | None = None,
        leader_tools: list[ToolSource] | None = None,
        max_iterations: int = 15,
        blackboard: type[BaseModel] | BaseModel | None = None,
        custom_prompt: str | None = None,
    ):
        """
        任务规划策略初始化，Leader 利用工具箱在黑板上拆解任务、管理依赖并
        驱动 Member 执行。

        参数:
            leader_model: 规划节点 (Leader) 使用的大模型名称，若为空则默认继承全局。
            leader_tools: 挂载给规划节点 (Leader) 的专属附加工具列表。
            max_iterations: 引擎驱动的状态机最大迭代/循环次数，防止死循环。
            blackboard: (可选) 团队共享黑板。可传入 Schema 类型类，或直接传入带有初始数据的 Schema 实例对象。
            custom_prompt: 自定义系统提示词，用于覆盖默认的规划系统提示词。
        """  # noqa: E501
        super().__init__(custom_prompt=custom_prompt)
        self.leader_model = leader_model
        self.leader_tools = leader_tools or []
        self.max_iterations = max_iterations

        self.blackboard = None
        self.bb_toolkit = None
        if blackboard is not None:
            schema = None
            initial_state = None
            if isinstance(blackboard, type) and issubclass(blackboard, BaseModel):
                schema = blackboard
            elif isinstance(blackboard, BaseModel):
                schema = type(blackboard)
                initial_state = blackboard
            else:
                raise ValueError(
                    "blackboard 参数必须是 Pydantic BaseModel 的子类(类型)或其实例"
                )

            self.blackboard = BlackboardManager(
                schema=schema, initial_state=initial_state
            )
            self.bb_toolkit = BlackboardToolkit(self.blackboard)

    def get_member_capabilities(
        self, team: "Team", member: BaseRunnable
    ) -> list[AbstractCapability]:
        caps = super().get_member_capabilities(team, member)
        if self.bb_toolkit:

            class BlackboardInjectCapability(AbstractCapability):
                def __init__(self, tk):
                    self.toolkit = tk

                async def get_tools(self, context: RunContext) -> list[Any]:
                    return [self.toolkit]

            caps.append(BlackboardInjectCapability(self.bb_toolkit))
        return caps

    async def generate_plan(
        self,
        team: "Team",
        intent: RunIntent,
        context: RunContext,
        **kwargs,
    ) -> AsyncGenerator[TeamAction, Any]:
        if self.blackboard is not None:
            context.session.blackboard = self.blackboard

        member_infos = []
        for m in team.members:
            desc = m.profile_summary
            member_infos.append(
                f'<member id="{m.name}" name="{m.name}">\n'
                f"  Description: {desc}\n"
                f"</member>"
            )

        members_xml = "<team_members>\n" + "\n".join(member_infos) + "\n</team_members>"

        leader_agent = self._build_leader_agent(
            team=team,
            role_name="Planner",
            extra_instruction=members_xml,
            extra_tools=[TaskPlanningToolkit(members=team.members)],
        )

        logger.debug(
            f"📋 [TaskStrategy] '{team.name}' 正在启动 Engine-Driven 状态机循环..."
        )

        if "__task_board__" not in context.session.shared_state:
            context.session.shared_state["__task_board__"] = TaskBoardState()
        board = cast(TaskBoardState, context.session.shared_state["__task_board__"])

        max_iterations = self.max_iterations
        planner_prompt = intent.original_input

        for iteration in range(max_iterations):
            if board.is_goal_complete:
                yield FinishAction(result=board.final_summary or "目标已标记完成。")
                return

            available_tasks = board.get_available_tasks()

            if not available_tasks:
                if iteration > 0:
                    board_str = board.render_board_to_string()
                    goal_str = intent.text

                    planner_prompt = f"""### 🎯 用户的终极目标 (Original Goal)
{goal_str}

### 📋 当前看板最新状态
{board_str}

**系统指令**：底层执行引擎的回合已结束。当前没有可立即执行的 pending 任务。
请检查是否有 failed 的任务需要修复重新指派？或者如果所有任务均已 completed，
请立刻调用 `mark_all_complete` 汇报总结。"""

                logger.debug(f"🧠 [TaskStrategy] 唤醒 Planner (Iter: {iteration})")
                leader_res = yield CallAction(
                    agent=leader_agent, task=planner_prompt or ""
                )

                if board.is_goal_complete:
                    yield FinishAction(result=board.final_summary or leader_res.output)
                    return
                continue

            logger.debug(
                f"🚀 [TaskStrategy] 引擎接管：并发执行 {len(available_tasks)} 个任务..."
            )
            actions = []
            valid_tasks = []

            for task in available_tasks:
                member_agent = next(
                    (m for m in team.members if m.name == task.assignee), None
                )
                if not member_agent:
                    board.update_task_status(
                        task.id,
                        TaskNodeStatus.failed,
                        f"执行异常: 找不到名为 '{task.assignee}' 的专家。",
                    )
                    continue

                board.update_task_status(task.id, TaskNodeStatus.in_progress)
                logger.debug(f"  🔄 [任务状态变更] `{task.title}` -> in_progress")

                task_prompt = f"### 🎯 你被指派的任务目标：\n{task.description}"

                if task.result:
                    task_prompt += (
                        f"\n\n### 💡 项目经理的补充建议/历史反馈：\n{task.result}"
                    )
                if task.dependencies:
                    dep_results = []
                    for dep_id in task.dependencies:
                        dep_task = board.get_task(dep_id)
                        if dep_task and dep_task.result:
                            dep_results.append(
                                f"【前置任务 [{dep_task.title}] 的产出】:\n"
                                f"{dep_task.result}"
                            )
                    if dep_results:
                        task_prompt += (
                            "\n\n### 📦 你的任务依赖以下前置结果，请基于此进行处理：\n"
                            + "\n\n".join(dep_results)
                        )

                if task.metadata:
                    meta_str = json.dumps(task.metadata, ensure_ascii=False)
                    task_prompt += f"\n\n### ⚙️ 附加系统元数据约束：\n{meta_str}"

                actions.append(CallAction(agent=member_agent.name, task=task_prompt))
                valid_tasks.append(task)

            if not actions:
                continue

            results = yield ConcurrentCallAction(actions=actions)

            for task, (agent_name, agent_res) in zip(valid_tasks, results):
                if isinstance(agent_res, BaseException):
                    output_str = f"❌ 专家框架级崩溃: {agent_res}"
                    board.update_task_status(task.id, TaskNodeStatus.failed, output_str)
                    final_status = "failed"
                else:
                    output_str = str(agent_res.output)
                    if output_str.startswith("Error:") or output_str.startswith("❌"):
                        board.update_task_status(
                            task.id, TaskNodeStatus.failed, output_str
                        )
                        final_status = "failed"
                    else:
                        board.update_task_status(
                            task.id, TaskNodeStatus.completed, output_str
                        )
                        final_status = "completed"

                logger.debug(f"  🔄 [任务状态变更] `{task.title}` -> {final_status}")

        yield FinishAction(
            result=f"达到最大迭代次数 ({max_iterations})，任务未能在限定步数内完成。"
        )

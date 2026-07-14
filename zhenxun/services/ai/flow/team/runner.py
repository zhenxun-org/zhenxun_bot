import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .team import Team

from zhenxun.services.ai.core.exceptions import (
    AbortException,
    ControlFlowExit,
    LLMException,
)
from zhenxun.services.ai.core.messages import UsageInfo
from zhenxun.services.ai.core.stream_events import AgentStreamEvent
from zhenxun.services.ai.flow.agent.models import AgentConfig
from zhenxun.services.ai.run import AgentRunResult, RunContext, RunIntent
from zhenxun.services.ai.run.models import AgentRunEnd
from zhenxun.services.ai.utils.logger import log_team as logger
from zhenxun.utils.pydantic_compat import model_construct

from .models import (
    CallAction,
    ConcurrentCallAction,
    FinishAction,
)
from .strategy import BaseTeamStrategy


class TeamRunner:
    """
    多智能体团队核心执行引擎。
    """

    def __init__(self, team: "Team", strategy: BaseTeamStrategy):
        self.team = team
        self.strategy = strategy

    async def _consume_event_queue(
        self,
        queue: asyncio.Queue,
        tasks: list[asyncio.Task],
        expected_results: int,
        results_box: dict[int, tuple[str, AgentRunResult]],
    ) -> AsyncGenerator[AgentStreamEvent, None]:
        """辅助方法：统一消费队列中的流事件、异常和结果"""
        try:
            while len(results_box) < expected_results:
                msg_type, *payload = await queue.get()
                if msg_type == "yield_event":
                    yield payload[0]
                elif msg_type == "control_flow_error":
                    raise payload[0]
                elif msg_type == "result":
                    idx, agent_name, agent_res = payload
                    results_box[idx] = (agent_name, agent_res)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    async def _execute_call_action_to_queue(
        self,
        index: int,
        action: CallAction,
        context: RunContext,
        session_id: str,
        queue: asyncio.Queue,
    ):
        """
        辅助方法：执行单一 Agent 任务，
        并将内部产生的 UI 事件与最终结果通过队列透传回主线程
        """
        if isinstance(action.agent, str):
            target_agent = next(
                (m for m in self.team.members if m.name == action.agent), None
            )
            if not target_agent:
                logger.error(f"❌ 找不到团队成员: {action.agent}")
                await queue.put(
                    (
                        "result",
                        (
                            action.agent,
                            AgentRunResult(
                                output=f"Error: {action.agent} not found",
                                usage=UsageInfo(),
                            ),
                        ),
                    )
                )
                return
        else:
            target_agent = action.agent

        sub_context = context.clone_for_member(target_agent.name)
        sub_context.capabilities = list(sub_context.capabilities)

        sub_context.capabilities.extend(
            self.strategy.get_member_capabilities(self.team, target_agent)
        )

        logger.debug(f"🚀 **专员 👨💼`{target_agent.name}`** 开始执行子任务...")

        agent_res = None

        try:
            async with target_agent.run_stream(
                prompt=action.task,
                context=sub_context,
                config=AgentConfig(message_history=action.history),
                **(action.kwargs or {}),
            ) as stream_result:
                async for event in stream_result.stream_events():
                    if isinstance(event, AgentRunEnd):
                        agent_res = event.result
                    else:
                        await queue.put(("yield_event", event))
        except ControlFlowExit as cfe:
            if isinstance(cfe, AbortException):
                await queue.put(("control_flow_error", cfe))
                return
            else:
                logger.debug(
                    f"Agent {target_agent.name} 触发局部控制流: "
                    f"{type(cfe).__name__} - {cfe}"
                )
                agent_res = AgentRunResult(output=str(cfe), usage=UsageInfo())
        except Exception as e:
            logger.error(f"Agent {target_agent.name} 执行崩溃: {e}")

            if isinstance(e, LLMException):
                abort_msg = getattr(e, "user_friendly_message", str(e))
                display_msg = (
                    f"❌ 智能体 {target_agent.name} 执行发生致命故障: {abort_msg}"
                )
                abort_err = AbortException(
                    reason=str(e),
                    display=display_msg,
                )
                await queue.put(("control_flow_error", abort_err))
                return

            agent_res = AgentRunResult(output=f"Error: {e}", usage=UsageInfo())

        if agent_res and agent_res.handoff:
            target_name = agent_res.handoff.target
            reason = agent_res.handoff.reason

            logger.debug(
                f"🛣️ **路由决策**: 委派给专员 👨💼`{target_name}` (理由: {reason})"
            )

        if not agent_res:
            agent_res = AgentRunResult(
                output="Error: No result returned", usage=UsageInfo()
            )

        logger.debug(f"✅ **专员 👨💼`{target_agent.name}`** 完成任务！")

        await queue.put(("result", index, target_agent.name, agent_res))

    async def _dispatch_actions(
        self,
        actions: list[CallAction],
        context: RunContext,
        session_id: str,
        results_container: list,
    ) -> AsyncGenerator[AgentStreamEvent, None]:
        """统一的任务派发、流事件转译与结果回传调度器"""
        queue = asyncio.Queue()
        tasks = [
            asyncio.create_task(
                self._execute_call_action_to_queue(i, act, context, session_id, queue)
            )
            for i, act in enumerate(actions)
        ]
        results_box: dict[int, tuple[str, AgentRunResult]] = {}
        async for event in self._consume_event_queue(
            queue, tasks, len(actions), results_box
        ):
            yield event
        results_container.extend([results_box[i] for i in range(len(actions))])

    async def run_stream(
        self, intent: RunIntent, context: RunContext, **kwargs: Any
    ) -> AsyncGenerator[AgentStreamEvent, None]:
        session_id = context.session_id or "default_team_session"
        task_desc = intent.text

        logger.debug(f"🤝 **团队 [{self.team.name}] 开始协作**: `{task_desc}`")

        plan_gen = self.strategy.generate_plan(self.team, intent, context, **kwargs)

        send_value = None
        final_result = None
        cumulative_usage = UsageInfo()

        try:
            while True:
                try:
                    action = await plan_gen.asend(send_value)
                except StopAsyncIteration:
                    break

                if isinstance(action, CallAction):
                    res_container = []
                    async for event in self._dispatch_actions(
                        [action], context, session_id, res_container
                    ):
                        yield event
                    _, send_value = res_container[0]
                    cumulative_usage += send_value.usage

                elif isinstance(action, ConcurrentCallAction):
                    res_container = []
                    async for event in self._dispatch_actions(
                        action.actions, context, session_id, res_container
                    ):
                        yield event
                    send_value = res_container
                    for _, res in res_container:
                        cumulative_usage += res.usage

                elif isinstance(action, FinishAction):
                    final_result = action.result
                    break
                else:
                    raise ValueError(f"TeamRunner 遇到了未知的动作类型: {type(action)}")

        except Exception as e:
            raise e

        logger.debug(f"🏁 **团队 [{self.team.name}]** 协作圆满结束！")

        if not isinstance(final_result, AgentRunResult):
            final_result = model_construct(
                AgentRunResult, output=final_result, usage=cumulative_usage
            )
        else:
            final_result.usage += cumulative_usage

        yield AgentRunEnd(result=final_result)

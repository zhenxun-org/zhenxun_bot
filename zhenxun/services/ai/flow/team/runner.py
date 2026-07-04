import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from zhenxun.services.ai.core.exceptions import (
    AbortException,
    ControlFlowExit,
    LLMException,
)
from zhenxun.services.ai.core.messages import UsageInfo
from zhenxun.services.ai.flow.team.capabilities import TeamRoutingCapability
from zhenxun.services.ai.flow.team.models import (
    CallAction,
    ConcurrentCallAction,
    FinishAction,
)
from zhenxun.services.ai.flow.team.strategy import BaseTeamStrategy
from zhenxun.services.ai.run import AgentRunResult, RunContext
from zhenxun.services.ai.run.models import AgentRunEnd
from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_construct


class TeamRunner:
    """
    多智能体团队核心执行引擎。
    """

    def __init__(self, team: Any, strategy: BaseTeamStrategy):
        self.team = team
        self.strategy = strategy

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
                logger.error(f"❌ [TeamRunner] 找不到团队成员: {action.agent}")
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

        from zhenxun.services.ai.flow.team.strategy import RouteStrategy

        if isinstance(self.strategy, RouteStrategy):
            routing_cap = TeamRoutingCapability(
                team_name=self.team.name,
                members=self.team.members,
                state_flow=getattr(self.strategy, "state_flow", None),
            )
            sub_context.capabilities.append(routing_cap)

        logger.debug(f"🚀 **专员 👨💼`{target_agent.name}`** 开始执行子任务...")

        agent_res = None

        try:
            from zhenxun.services.ai.flow.agent.models import AgentConfig

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

            logger.info(
                f"🛣️ **路由决策**: 委派给专员 👨💼`{target_name}` (理由: {reason})"
            )

        if not agent_res:
            agent_res = AgentRunResult(
                output="Error: No result returned", usage=UsageInfo()
            )

        logger.debug(f"✅ **专员 👨💼`{target_agent.name}`** 完成任务！")

        await queue.put(("result", index, target_agent.name, agent_res))

    async def run_stream(
        self, prompt: Any, context: RunContext, **kwargs: Any
    ) -> AsyncGenerator[Any, None]:
        session_id = context.session_id or "default_team_session"
        task_desc = getattr(prompt, "description", str(prompt))

        logger.info(f"🤝 **团队 [{self.team.name}] 开始协作**: `{task_desc}`")

        plan_gen = self.strategy.generate_plan(self.team, prompt, context, **kwargs)

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
                    queue = asyncio.Queue()
                    task = asyncio.create_task(
                        self._execute_call_action_to_queue(
                            0, action, context, session_id, queue
                        )
                    )
                    try:
                        while True:
                            msg_type, *payload = await queue.get()
                            if msg_type == "yield_event":
                                yield payload[0]
                            elif msg_type == "control_flow_error":
                                raise payload[0]
                            elif msg_type == "result":
                                idx, agent_name, agent_res = payload
                                send_value = agent_res
                                cumulative_usage += agent_res.usage
                                break
                    finally:
                        if not task.done():
                            task.cancel()

                elif isinstance(action, ConcurrentCallAction):
                    queue = asyncio.Queue()
                    tasks = []
                    for i, act in enumerate(action.actions):
                        tasks.append(
                            asyncio.create_task(
                                self._execute_call_action_to_queue(
                                    i, act, context, session_id, queue
                                )
                            )
                        )

                    results_dict = {}
                    try:
                        while len(results_dict) < len(action.actions):
                            msg_type, *payload = await queue.get()
                            if msg_type == "yield_event":
                                yield payload[0]
                            elif msg_type == "control_flow_error":
                                for t in tasks:
                                    t.cancel()
                                raise payload[0]
                            elif msg_type == "result":
                                idx, agent_name, agent_res = payload
                                results_dict[idx] = (agent_name, agent_res)
                                cumulative_usage += agent_res.usage
                        send_value = [
                            results_dict[i] for i in range(len(action.actions))
                        ]
                    finally:
                        for task in tasks:
                            if not task.done():
                                task.cancel()

                elif isinstance(action, FinishAction):
                    final_result = action.result
                    break
                else:
                    raise ValueError(f"TeamRunner 遇到了未知的动作类型: {type(action)}")

        except Exception as e:
            raise e

        logger.info(f"🏁 **团队 [{self.team.name}]** 协作圆满结束！")

        if not isinstance(final_result, AgentRunResult):
            final_result = model_construct(
                AgentRunResult, output=final_result, usage=cumulative_usage
            )
        else:
            final_result.usage += cumulative_usage

        yield AgentRunEnd(result=final_result)

import asyncio
from collections.abc import AsyncIterator
import contextlib
from typing import TYPE_CHECKING, Any
import uuid

from nonebot.params import Depends

if TYPE_CHECKING:
    from zhenxun.services.ai.flow.workflow.nodes import NodeSource

from zhenxun.services.ai.core.exceptions import ControlFlowExit, ToolRetryError
from zhenxun.services.ai.core.messages import PromptInput, UsageInfo
from zhenxun.services.ai.core.stream_events import EventBus
from zhenxun.services.ai.flow.base import BaseRunnable, BaseRuntimeConfig
from zhenxun.services.ai.flow.workflow.nodes import Steps
from zhenxun.services.ai.flow.workflow.types import (
    StepInput,
    StepOutput,
    WorkflowRunResult,
)
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.run.models import (
    AgentRunEnd,
    AgentRunError,
    AgentRunResult,
    StreamedRunResult,
)
from zhenxun.services.ai.tools.core.tool import FunctionTool
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils


class Workflow(BaseRunnable[WorkflowRunResult]):
    """
    工作流顶层容器 (The Workflow Facade)。
    继承自 BaseRunnable，支持被作为节点嵌套在 Team 或 其他工作流中。
    """

    def __init__(self, name: str, steps: list["NodeSource"], description: str = ""):
        """
        静态图元工作流容器初始化。

        参数:
            name: 工作流的名称标识。
            steps: 工作流的节点列表（按列表顺序构成串行或嵌套结构）。
            description: 工作流的说明描述，用于被 Agent 调用时理解其功能。
        """
        self.name = name
        self.description = description
        self.id = uuid.uuid4().hex

        self.root_steps = Steps(steps=steps, name=f"{self.name}_Root")
        self.runtime_config = BaseRuntimeConfig(stateless=True)
        self.persona = None

    def _build_result(
        self,
        initial_input: StepInput,
        safe_context: RunContext,
        final_output: StepOutput,
    ) -> WorkflowRunResult:
        """根据执行链上的全量输出构建最终的工作流执行结果对象"""
        flat_outputs = {}

        def _extract(out: StepOutput):
            flat_outputs[out.step_name] = out
            if out.steps:
                for o in out.steps:
                    _extract(o)

        if final_output:
            _extract(final_output)

        status = "completed" if final_output and final_output.success else "error"

        return WorkflowRunResult(
            workflow_id=self.id,
            workflow_name=self.name,
            status=status,
            original_input=initial_input.input,
            state=safe_context.state,
            step_outputs=flat_outputs,
            last_step_content=final_output.content if final_output else None,
            final_output=final_output,
        )

    def bind(self, **kwargs: Any) -> Any:
        """DI 注入语法糖"""

        async def _dependency() -> "Workflow":
            return self

        return Depends(_dependency)

    async def reply(
        self, prompt: PromptInput | None = None, reply_to: bool = False, **kwargs: Any
    ) -> WorkflowRunResult:
        """
        工作流交互执行语法糖，隐式提取上下文并自动将最终流水线产出发送回复给用户。

        参数:
            prompt: 传入工作流入口根节点的初始参数或指令。
            reply_to: 是否将结果作为回复消息发送 (at用户或引用原消息)。
            kwargs: 追加的工作流附带参数 (additional_data)。

        返回:
            WorkflowRunResult: 包含执行状态、断点快照、各节点产出的全量工作流结果对象。
        """
        ctx = RunContext()
        bot = ctx.get_bot()
        event = ctx.get_event()

        res = await self.run(prompt=prompt, context=ctx, **kwargs)

        if bot and event:
            if res.status == "completed" and res.final_output:
                msg = (
                    str(res.final_output.content)
                    if res.final_output.content
                    else "执行完毕"
                )
                await MessageUtils.build_message(msg).send(reply_to=reply_to)
            elif res.status == "error":
                err_msg = res.final_output.error if res.final_output else "未知异常"
                await MessageUtils.build_message(
                    f"❌ 工作流执行发生错误: {err_msg}"
                ).send(reply_to=reply_to)

        return res

    async def run(
        self,
        prompt: PromptInput | None = None,
        *,
        context: RunContext | None = None,
        **kwargs: Any,
    ) -> WorkflowRunResult:
        """
        工作流单次运行阻塞核心入口，遍历所有图元节点直至终止。

        参数:
            prompt: 传入工作流入口根节点的初始参数或指令。
            context: 显式传入的会话与运行上下文。
            kwargs: 追加的工作流附带参数 (additional_data)。

        返回:
            WorkflowRunResult: 包含执行状态、断点快照、各节点产出的全量工作流结果对象。
        """
        session_id = (
            context.session_id if context and context.session_id else f"wf_{self.id}"
        )
        safe_context = context or RunContext(session_id=session_id)

        logger.debug(f"🏭 **工作流 [{self.name}] 启动**")

        initial_input = StepInput(input=prompt)
        if kwargs:
            initial_input.additional_data.update(kwargs)

        try:
            final_output = await self.root_steps.aexecute(initial_input, safe_context)

            logger.debug(f"🏭 **工作流 [{self.name}] 运行结束**")

            return self._build_result(initial_input, safe_context, final_output)

        except BaseException as e:
            if isinstance(e, ControlFlowExit):
                logger.debug(f"⏭️ 工作流执行被业务控制流安全中止: {e}")
                dummy_output = StepOutput(content=str(e), success=False)
                return self._build_result(initial_input, safe_context, dummy_output)

            raise e

    @contextlib.asynccontextmanager
    async def run_stream(
        self,
        prompt: PromptInput | None = None,
        *,
        context: RunContext | None = None,
        **kwargs: Any,
    ) -> AsyncIterator["StreamedRunResult[Any]"]:
        """对齐 BaseRunnable 接口的流式上下文管理器"""
        event_bus = EventBus()
        if context:
            context.run.event_bus = event_bus

        async def _execution_task():
            try:
                async for event in self._internal_stream(prompt, context, **kwargs):
                    await event_bus.emit(event)
            except BaseException as e:
                await event_bus.emit(AgentRunError(error=e))
            finally:
                await event_bus.end()

        task = asyncio.create_task(_execution_task())
        try:
            yield StreamedRunResult[Any](event_bus)
        finally:
            if not task.done():
                task.cancel()

    async def _internal_stream(
        self,
        prompt: PromptInput | None = None,
        context: RunContext | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """流式执行工作流节点树的内部实现"""
        session_id = (
            context.session_id if context and context.session_id else f"wf_{self.id}"
        )
        safe_context = context or RunContext(session_id=session_id)

        logger.debug(f"🏭 **工作流 [{self.name}] 启动**")

        initial_input = StepInput(input=prompt)
        if kwargs:
            initial_input.additional_data.update(kwargs)

        try:
            final_output = None
            async for event in self.root_steps.aexecute_stream(
                initial_input, safe_context
            ):
                if isinstance(event, StepOutput):
                    final_output = event
                else:
                    yield event

            if final_output:
                logger.debug(f"🏭 **工作流 [{self.name}] 运行结束**")

                wf_result = self._build_result(
                    initial_input, safe_context, final_output
                )
                agent_res = AgentRunResult(
                    output=wf_result.last_step_content,
                    structured_data=wf_result,
                    usage=UsageInfo(),
                )
                yield AgentRunEnd(result=agent_res)
        except Exception:
            pass

    def as_tool(self, tool_name: str | None = None) -> FunctionTool:
        """将工作流封装并导出为可供 Agent 直接调用的 FunctionTool 实例"""

        async def _execute_workflow_tool(prompt: str, context: RunContext) -> str:
            run_result = await self.run(prompt=prompt, context=context)
            output = run_result.final_output

            if output and output.success:
                return (
                    f"工作流 [{self.name}] 执行完毕。最终流水线产出:\n{output.content}"
                )

            raise ToolRetryError(
                f"工作流执行失败: {output.error if output else 'unknown'}，"
                "请尝试换种方式处理。"
            )

        final_tool_name = tool_name or f"trigger_workflow_{self.id}"

        tool_desc = (
            f"触发执行专属流水线: {self.name}。\n"
            f"描述: {self.description}\n"
            f"注意：如果你认为该工作流能完全解决用户的问题，请立刻调用此工具，"
            f"并将用户的诉求提炼后作为 prompt 传入。"
        )

        return FunctionTool(
            func=_execute_workflow_tool, name=final_tool_name, description=tool_desc
        )

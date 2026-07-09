from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from zhenxun.services.ai.core.exceptions import AbortException, ControlFlowExit
from zhenxun.services.ai.core.stream_events import ToolStreamChunkEvent
from zhenxun.services.ai.flow.base import BaseRunnable
from zhenxun.services.ai.run import RunContext
from zhenxun.services.ai.tools.core.tool import BaseTool
from zhenxun.services.ai.tools.models import ToolResult
from zhenxun.services.ai.utils.logger import log_tool as logger
from zhenxun.utils.pydantic_compat import model_dump

STRUCTURED_INPUT_PREAMBLE = (
    "\n\n### 🛠️ [嵌套调用前置语境]\n"
    "你现在正在作为一个『工具/子节点』被外部主智能体调用。\n"
    "以下是外部系统传递给你的结构化输入数据：\n"
    "```json\n{payload}\n```\n"
    "请严格将上述内容视为你的核心数据和约束条件，专注于解决该子任务，并直接返回结果，不要说多余的废话。\n"
)


class DelegateArgs(BaseModel):
    task: str = Field(
        ..., description="指派给该实体的具体任务描述、指令或需要回答的问题"
    )


class DelegateTool(BaseTool):
    """将可运行实体 (Agent/Team/Workflow) 包装为子例程委派工具。"""

    def __init__(
        self,
        runnable: BaseRunnable[Any],
        name: str | None = None,
        description: str | None = None,
        max_delegations: int = 3,
    ):
        """初始化委派工具。

        参数:
            runnable: 被包装的可运行实体。
            name: 自定义工具名称。
            description: 工具描述信息。
            max_delegations: 允许向同一个实体连续委派且未获成功的最大次数限制。
        """
        resolved_name = name or getattr(runnable, "name", "SubRunnable")
        resolved_desc = description or getattr(
            runnable, "description", f"将子任务委派给 {resolved_name} 执行"
        )
        final_name = (
            f"delegate_to_{resolved_name}"
            if not resolved_name.startswith("delegate_")
            else resolved_name
        )
        super().__init__(name=final_name, description=resolved_desc)
        self.runnable = runnable
        self.args_schema = DelegateArgs
        self.max_delegations = max_delegations

    async def execute(
        self, context: RunContext | None = None, **kwargs: Any
    ) -> ToolResult:
        task = kwargs.get("task", "")
        context = context or RunContext()

        counts = context.session.shared_state.setdefault("__delegate_counts__", {})
        counts[self.name] = counts.get(self.name, 0) + 1
        if counts[self.name] > self.max_delegations:
            return ToolResult(
                output=(
                    f"❌ 系统拦截：委派重试次数已达上限。\n"
                    f"你已经连续 {counts[self.name]} 次将子任务委派给下级实体 "
                    f"{self.name} 且未获最终成功"
                    f"（超出最大允许次数 {self.max_delegations}）。\n"
                    "请立即停止委派，"
                    "改变你的思考方向或直接向用户汇报失败结论！"
                )
            ).as_error()

        depth = context.run.delegate_depth
        if depth >= 3:
            logger.warning(
                f"⚠️ [DelegateTool] 委派深度超限 ({depth})，强制阻断: {self.name}"
            )
            raise AbortException(
                reason="嵌套层级过深，系统已强制拒绝执行委派",
                display=f"⚠️ {self.name} 嵌套层级过深",
            )

        logger.debug(
            f"🔄 [DelegateTool] 正在委派下级实体 {self.name} (Task: {task[:30]}...)"
        )

        sub_context = context.clone_for_member(self.name)
        sub_context.run.delegate_depth = depth + 1

        payload_str = json.dumps(kwargs, ensure_ascii=False, indent=2)
        preamble = STRUCTURED_INPUT_PREAMBLE.format(payload=payload_str)
        sub_context.run.add_system_prompt(preamble)

        try:
            event_bus = context.run.event_bus
            async with self.runnable.run_stream(
                prompt=task,
                context=sub_context,
            ) as stream_result:
                response = await stream_result.forward_to(event_bus, self.name)

            if response is None:
                raise RuntimeError(f"Sub-agent {self.name} did not return a response.")

            if isinstance(response.output, BaseModel):
                final_output = model_dump(response.output)
            else:
                final_output = response.output

            if response.handoff:
                final_output = (
                    f"⚠️ 子任务未完成。下级实体主动发起了工作流移交 (Handoff)。\n"
                    f"移交目标: {response.handoff.target}\n"
                    f"移交原因: {response.handoff.reason}\n"
                    f"附带数据: {response.handoff.context_data}"
                )

            is_fatal = isinstance(final_output, str) and (
                "DepthLimitExceeded" in final_output or "嵌套层级过深" in final_output
            )
            if is_fatal:
                raise AbortException(
                    reason="下级实体遇到深度限制异常",
                    display=f"⚠️ 实体 {self.name} 委派失败",
                )

            usage = getattr(response, "usage", None)

            if context:
                await context.run.emit(
                    ToolStreamChunkEvent(
                        tool_name=self.name, content=f"🧠 实体 {self.name} 执行完毕"
                    )
                )

            return ToolResult(
                output=final_output,
                usage=usage,
            )
        except ControlFlowExit as e:
            raise e
        except Exception as e:
            logger.error(f"委派实体 {self.name} 执行失败: {e}", e=e)
            raise AbortException(
                reason=f"Delegate Execution Error: {e}",
                display=f"❌ 实体 {self.name} 执行异常",
            )

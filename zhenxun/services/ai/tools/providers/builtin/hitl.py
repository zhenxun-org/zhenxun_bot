from zhenxun.services.ai.core.exceptions import AbortException, ToolFatalError
from zhenxun.services.ai.core.stream_events import ToolStreamChunkEvent
from zhenxun.services.ai.run import Inject, RunContext
from zhenxun.services.ai.tools.core.decorators import tool
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import ToolResult
from zhenxun.services.log import logger


class HITLToolkit(BaseToolkit):
    """
    Human-in-the-Loop (HITL) 工具箱。
    提供大模型主动挂起执行流并向用户提问求助的能力。
    """

    default_prefix = ""

    default_instructions = (
        "## 人机协同求助系统\n"
        "你拥有向用户发起提问的权限。当你遇到困难时（例如缺少关键信息、连续执行某个操作出错、找不到文件等），"
        "可以使用 `ask_user_for_help` 工具请求用户协助。"
        "用户回答后，你将收到答案并可以继续任务。"
    )

    @tool(
        name="ask_user_for_help",
        description="向当前对话的用户提出问题以获取信息或指导。当你无法独立完成任务时，请调用此工具。",
    )
    async def ask_user_for_help(
        self,
        question: str,
        hitl: Inject.HITL,
        context: RunContext,
    ) -> ToolResult:
        try:
            user_reply = await hitl.ask_text(
                f"🤖 [AI 提问]\n{question}\n\n(请在 60 秒内回复，或回复'取消')",
                timeout=60.0,
            )
        except ToolFatalError:
            return ToolResult(
                output="用户未在规定时间内回复 (超时未响应)，"
                "你必须尝试自行解决或中止任务。",
            ).as_error()
        except AbortException as e:
            raise e

        logger.info(f"收到用户求助回复: {user_reply}")
        if context and context.run.event_bus:
            await context.run.event_bus.emit(
                ToolStreamChunkEvent(
                    tool_name=context.call.tool_name, content="🗣️ 已收到用户的回复"
                )
            )

        return ToolResult(output=f"用户提供的回复是: {user_reply}")

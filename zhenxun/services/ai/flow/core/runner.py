import asyncio
from typing import Any, Generic, cast
from typing_extensions import TypeVar
import uuid

from nonebot.adapters import Bot, Event
from nonebot_plugin_alconna.uniseg import UniMessage

from zhenxun.services.ai.core.exceptions import (
    ConcurrencyInterruptException,
    ConcurrencyRejectException,
    ControlFlowExit,
    InterventionHandledException,
)
from zhenxun.services.ai.core.messages import PromptInput, UsageInfo
from zhenxun.services.ai.flow.core.base import BaseRunnable
from zhenxun.services.ai.run import AgentRunResult, RunContext
from zhenxun.services.ai.run.models import AgentRunEnd, AgentRunError, AgentTask
from zhenxun.services.ai.run.ui import UIController
from zhenxun.services.ai.utils.logger import log_flow as logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

T_Deps = TypeVar("T_Deps", default=Any)
T_Out = TypeVar("T_Out", default=str)


class FlowRunner(Generic[T_Out]):
    """
    执行流交互运行器。
    负责将大模型的纯净数据流包装为平台交互动作（发消息、UI渲染）。
    自带 ContextVars 隐式上下文提取魔法。
    """

    def __init__(
        self,
        runnable: BaseRunnable,
        context: RunContext | None = None,
        **kwargs: Any,
    ):
        self.runnable = runnable
        self.context = context or RunContext(**kwargs)

        is_stateless = (
            getattr(self.runnable.runtime_config, "stateless", True)
            if hasattr(self.runnable, "runtime_config")
            else True
        )
        if is_stateless and self.context.session_id:
            if not self.context.session_id.startswith("stateless_"):
                self.context.session_id = (
                    f"stateless_{self.context.session_id}_{uuid.uuid4().hex[:8]}"
                )
                if self.context.session:
                    self.context.session.session_id = self.context.session_id

    @property
    def _bot(self) -> Bot | None:
        return self.context.get_bot()

    @property
    def _event(self) -> Event | None:
        return self.context.get_event()

    async def reply(
        self,
        prompt: PromptInput | AgentTask | None = None,
        reply_to: bool = False,
        **kwargs: Any,
    ) -> AgentRunResult[T_Out]:
        """交互式执行：将 Agent 运行过程中的工具调用状态和最终结果自动发送给用户。"""
        final_result = None

        profile = kwargs.pop("profile", None)

        try:
            async with self.runnable.run_stream(
                prompt=prompt,
                context=self.context,
                profile=profile,
                **kwargs,
            ) as stream_result:
                async for stream_event in stream_result.stream_events():
                    if isinstance(stream_event, AgentRunEnd):
                        final_result = stream_event.result

                    elif isinstance(stream_event, AgentRunError):
                        raise stream_event.error

        except ControlFlowExit as e:
            if isinstance(e, InterventionHandledException):
                logger.info(f"✨ {self.runnable.name} 触发运行时干预: {e.message}")
                if e.display_content and self._bot and self._event:
                    await MessageUtils.build_message(str(e.display_content)).send(
                        reply_to=reply_to
                    )
                return cast(
                    AgentRunResult[T_Out], AgentRunResult(output="", usage=UsageInfo())
                )

            if isinstance(e, ConcurrencyRejectException):
                logger.warning(
                    f"⏳ {self.runnable.name} 触发并发拒绝 (REJECT): {e.message}"
                )
                return cast(
                    AgentRunResult[T_Out], AgentRunResult(output="", usage=UsageInfo())
                )

            if isinstance(e, ConcurrencyInterruptException):
                logger.warning(
                    f"🛑 {self.runnable.name} 触发并发中断 (INTERRUPT): {e.message}"
                )
                return cast(
                    AgentRunResult[T_Out], AgentRunResult(output="", usage=UsageInfo())
                )

            logger.debug(
                f"{self.runnable.name} 控制流正常中断: {type(e).__name__} - {e}"
            )
            await UIController.handle_control_flow_exit_display(
                e, self.context, reply_to
            )

            raise asyncio.CancelledError()

        except Exception as e:
            logger.error(f"{self.runnable.name} 运行失败: {e}", e=e)
            if self._bot and self._event:
                await MessageUtils.build_message(f"❌ 运行发生错误: {e}").send()
            raise e

        if final_result and final_result.output and self._bot:
            msg_to_send = (
                final_result.output
                if isinstance(final_result.output, UniMessage)
                else MessageUtils.build_message(str(final_result.output))
            )
            if self._event:
                await msg_to_send.send(self._event, bot=self._bot, reply_to=reply_to)
            else:
                target = PlatformUtils.get_target(
                    user_id=self.context.get_user_id(),
                    group_id=self.context.get_group_id(),
                )
                if target:
                    await msg_to_send.send(target=target, bot=self._bot)

            if isinstance(final_result.output, UniMessage):
                final_result.output = final_result.output.extract_plain_text()

        if final_result is None:
            raise RuntimeError("智能体运行流异常结束：未返回最终结果。")

        return cast(AgentRunResult[T_Out], final_result)

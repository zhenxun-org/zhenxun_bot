from __future__ import annotations

from typing import Any

from nonebot_plugin_alconna import UniMessage

from zhenxun.services.ai.core.stream_events import ToolStreamChunkEvent, UserCustomEvent
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

from .context import RunContext


class UIController:
    """前端 UI 富交互流式控制器 (按需生成模式)"""

    def __init__(self, context: RunContext):
        self.context = context

    @property
    def tool_name(self) -> str:
        """从上下文中动态获取当前调用的工具名"""
        return getattr(self.context.call, "tool_name", "UnknownTool")

    async def send_text(self, text: str, status: str = "running") -> None:
        """向前端流式反馈执行进度文本"""
        await self.context.run.emit(
            ToolStreamChunkEvent(
                tool_name=self.tool_name, content=text, metadata={"status": status}
            )
        )

    async def send_image(self, image: bytes | str) -> None:
        """向前端发送富文本图片气泡（字节流或 URL）"""
        display_msg = UniMessage()
        if isinstance(image, bytes):
            display_msg = display_msg.image(raw=image)
        else:
            display_msg = display_msg.image(url=image)
        await self.context.run.emit(UserCustomEvent(display=display_msg))

    async def send_display(self, display: Any) -> None:
        """向前端发送任意展示载体"""
        if display is not None:
            await self.context.run.emit(UserCustomEvent(display=display))

    @staticmethod
    async def handle_control_flow_exit_display(
        e: BaseException, context: RunContext | None, reply_to: bool = False
    ) -> None:
        """统一处理 ControlFlowExit 异常带来的 UI 反馈逻辑"""
        from zhenxun.services.ai.core.exceptions import ControlFlowExit

        if not isinstance(e, ControlFlowExit):
            return

        display_msg = getattr(e, "display", None) or getattr(e, "display_content", None)
        if not display_msg:
            return

        try:
            bot = context.get_bot() if context else None
            event = context.get_event() if context else None

            if bot:
                msg_to_send = (
                    display_msg
                    if isinstance(display_msg, UniMessage)
                    else MessageUtils.build_message(str(display_msg))
                )
                if event:
                    await msg_to_send.send(event, bot=bot, reply_to=reply_to)
                elif context:
                    target = PlatformUtils.get_target(
                        user_id=context.get_user_id(), group_id=context.get_group_id()
                    )
                    if target:
                        await msg_to_send.send(target=target, bot=bot)
            else:
                await MessageUtils.build_message(str(display_msg)).send(
                    reply_to=reply_to
                )
        except Exception:
            pass

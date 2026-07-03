import time
from typing import Any

from nonebot_plugin_alconna import UniMessage

from zhenxun.services.ai.core.messages import BaseContentPart, ImagePart, TextPart
from zhenxun.services.ai.core.stream_events import (
    EventBus,
    LLMEndEvent,
    LLMStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolStreamChunkEvent,
    UserCustomEvent,
)
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.run.models import AgentRunEnd, AgentRunStart, AgentRunSummary
from zhenxun.services.log import logger


class TelemetrySubscriber:
    """纯粹的数据观察者：默默记录时间戳与 Token 消耗"""

    def __init__(self):
        """初始化遥测数据观察者，准备记录统计摘要"""
        self.summary = AgentRunSummary()
        self._start_times: dict[str, float] = {}

    def attach(self, bus: EventBus):
        """注册监听的各种智能体生命周期和核心流程事件"""
        bus.subscribe(AgentRunStart, self.on_run_start)
        bus.subscribe(AgentRunEnd, self.on_run_end)
        bus.subscribe(LLMStartEvent, self.on_llm_start)
        bus.subscribe(LLMEndEvent, self.on_llm_end)
        bus.subscribe(ToolCallStartEvent, self.on_tool_start)
        bus.subscribe(ToolCallEndEvent, self.on_tool_end)

    async def on_run_start(self, event: AgentRunStart):
        """处理智能体运行开始事件，记录运行起始时间"""
        self._start_times["run"] = time.monotonic()
        logger.debug(f"🚀 [Telemetry] 智能体 {event.agent_name} 开始运行")

    async def on_run_end(self, event: AgentRunEnd):
        """处理智能体运行结束事件，统计总耗时并填充至结果"""
        dur = (time.monotonic() - self._start_times.get("run", time.monotonic())) * 1000
        self.summary.total_latency_ms = dur
        event.result.telemetry = self.summary
        logger.debug(f"🏁 [Telemetry] 智能体运行结束 (总耗时: {dur:.2f}ms)")

    async def on_llm_start(self, event: LLMStartEvent):
        """处理模型调用开始事件，记录单次大模型交互的起始时间"""
        self._start_times["llm"] = time.monotonic()

    async def on_llm_end(self, event: LLMEndEvent):
        """处理模型调用结束事件，累计大模型交互次数和延迟，并记录停止原因"""
        dur = (time.monotonic() - self._start_times.pop("llm", time.monotonic())) * 1000
        self.summary.chats.total += 1
        self.summary.chats.total_latency_ms += dur

        response = event.response
        if response:
            stop_reason = "tool_calls" if response.tool_calls else "stop"
            self.summary.chats.by_stop_reason[stop_reason] = (
                self.summary.chats.by_stop_reason.get(stop_reason, 0) + 1
            )
        logger.debug(f"🧠 [Telemetry] 模型调用完成 (耗时: {dur:.2f}ms)")

    async def on_tool_start(self, event: ToolCallStartEvent):
        """处理工具调用开始事件，记录指定工具执行的起始时间"""
        self._start_times[f"tool_{event.tool_name}"] = time.monotonic()

    async def on_tool_end(self, event: ToolCallEndEvent):
        """处理工具调用结束事件，统计工具执行耗时，累计成功和失败次数"""
        dur = (
            time.monotonic()
            - self._start_times.pop(f"tool_{event.tool_name}", time.monotonic())
        ) * 1000
        self.summary.tools.total += 1
        self.summary.tools.total_latency_ms += dur

        tool_stat = self.summary.tools.by_name.setdefault(
            event.tool_name, {"total": 0, "ok": 0, "error": 0, "latency_ms": 0.0}
        )
        tool_stat["total"] += 1
        tool_stat["latency_ms"] += dur

        if event.is_error:
            self.summary.tools.error += 1
            tool_stat["error"] += 1
        else:
            self.summary.tools.ok += 1
            tool_stat["ok"] += 1
        logger.debug(
            f"🛠️ [Telemetry] 工具 {event.tool_name} 执行完毕 (耗时: {dur:.2f}ms)"
        )


class DefaultUISubscriber:
    """纯粹的 UI 观察者：负责将特定事件转化为群聊气泡发送"""

    def __init__(
        self, context: RunContext, reply_to: bool = False, verbose: bool = False
    ):
        """
        初始化默认的 UI 观察者，绑定运行上下文并获取 bot 与事件实例。

        参数:
            context: 智能体单次运行的上下文对象，用于提取 bot、事件及依赖。
            reply_to: 在发送平台消息时是否对用户发起的消息进行回复（引用/艾特），默认 False。
            verbose: 是否开启冗长模式，若开启则会将工具流等过程事件也输出到平台，默认 False。
        """  # noqa: E501
        self.context = context
        self.reply_to = reply_to
        self.verbose = verbose
        self.bot = context.get_bot()
        self.event = context.get_event()

    def attach(self, bus: EventBus):
        """注册订阅相关的 UI 交互事件（如工具输出流、用户自定义事件等）"""
        bus.subscribe(ToolStreamChunkEvent, self.on_tool_stream)
        bus.subscribe(UserCustomEvent, self.on_custom_event)

    async def _send_to_platform(self, display: Any):
        """将通用的显示内容或富文本消息部件渲染并发送至具体的聊天平台"""
        if not self.bot or not self.event or not display:
            return

        if (
            isinstance(display, list)
            and len(display) > 0
            and isinstance(display[0], BaseContentPart)
        ):
            msg = UniMessage()
            for part in display:
                if isinstance(part, TextPart) and part.text:
                    msg = msg.text(part.text)
                elif isinstance(part, ImagePart):
                    if part.raw:
                        msg = msg.image(raw=part.raw)
                    elif part.url:
                        msg = msg.image(url=part.url)
                    elif part.path:
                        msg = msg.image(path=part.path)
            display = msg

        if isinstance(display, UniMessage):
            await display.send(self.event, bot=self.bot, reply_to=self.reply_to)
        else:
            await self.bot.send(self.event, str(display))

    async def on_tool_stream(self, event: ToolStreamChunkEvent):
        """处理工具流式数据块事件，仅在 verbose 开启时发送给平台"""
        if self.verbose and event.content:
            await self._send_to_platform(event.content)

    async def on_custom_event(self, event: UserCustomEvent):
        """处理用户自定义事件，将展示内容发送至平台"""
        await self._send_to_platform(event.display)

from __future__ import annotations

"""
运行时（Run）相关核心类型定义
"""

from collections.abc import AsyncIterator, Callable
import json
from typing import Any, Generic, cast
from typing_extensions import TypeVar

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from zhenxun.services.ai.core.messages import AgentMessage, LLMMessage, UsageInfo
from zhenxun.services.ai.core.options import BaseOutputDefinition
from zhenxun.services.ai.core.protocols.tool import ToolResolvable
from zhenxun.services.ai.core.stream_events import (
    AgentStreamEvent,
    EventBus,
    ToolCallStartEvent,
    ToolStreamChunkEvent,
)
from zhenxun.services.ai.guardrails import BaseGuardrail, GuardrailSource
from zhenxun.services.ai.tools.core.tool import BaseTool
from zhenxun.utils.pydantic_compat import model_dump, model_validator


class ChatSummary(BaseModel):
    total: int = 0
    """大模型调用总次数"""
    total_latency_ms: float = 0.0
    """大模型调用总耗时（毫秒）"""
    by_stop_reason: dict[str, int] = Field(default_factory=dict)
    """按停止原因分类的大模型调用计数"""


class ToolSummary(BaseModel):
    total: int = 0
    """工具执行总次数"""
    ok: int = 0
    """工具成功执行次数"""
    error: int = 0
    """工具执行失败次数"""
    total_latency_ms: float = 0.0
    """工具执行总耗时（毫秒）"""
    by_name: dict[str, dict[str, float | int]] = Field(default_factory=dict)
    """按工具名称细分的执行状态统计"""


class AgentRunSummary(BaseModel):
    """Agent 单次运行的全局可观测性遥测摘要"""

    chats: ChatSummary = Field(default_factory=ChatSummary)
    """大模型调用遥测摘要"""
    tools: ToolSummary = Field(default_factory=ToolSummary)
    """工具执行遥测摘要"""
    usage: UsageInfo = Field(default_factory=UsageInfo)
    """Token 消耗总计"""
    total_latency_ms: float = 0.0
    """智能体运行总耗时（毫秒）"""


class HandoffPayload(BaseModel):
    """移交信息载荷"""

    target: str
    """移交的目标智能体名称"""
    reason: str = ""
    """移交的原因描述"""
    context_data: Any = ""
    """随移交传递的上下文数据"""


OutputDataT = TypeVar("OutputDataT", default=str)


class AgentRunResult(BaseModel, Generic[OutputDataT]):
    """Agent 单次无状态运行的结果"""

    output: OutputDataT
    """最终输出数据"""
    messages: list[AgentMessage] = Field(default_factory=list)
    """本次运行产生/更新的历史消息"""
    usage: UsageInfo = Field(default_factory=UsageInfo)
    """本次运行的Token消耗总计"""
    structured_data: Any | None = None
    """拦截到的结构化结果字典"""
    telemetry: AgentRunSummary | None = None
    """单次运行的完整可观测性遥测摘要"""
    handoff: HandoffPayload | None = None
    """向外抛出的软移交载荷（存在时说明Agent发起了移交请求）"""

    class Config:
        arbitrary_types_allowed = True

    @property
    def llm_messages(self) -> list[LLMMessage]:
        """动态视图：过滤掉内部业务事件 (AgentEvent)，
        仅返回纯净的底层聊天消息历史"""
        return [m for m in self.messages if isinstance(m, LLMMessage)]


class AgentRunStart(AgentStreamEvent):
    """智能体运行开始"""

    agent_name: str
    """启动运行的智能体名称"""


class AgentRunError(AgentStreamEvent):
    """智能体运行发生异常"""

    error: BaseException
    """智能体运行过程中抛出的异常"""


class AgentRunEnd(AgentStreamEvent):
    """智能体运行完全结束"""

    result: AgentRunResult[Any]
    """智能体运行结束后的完整结果"""


class StreamedRunResult(Generic[OutputDataT]):
    """
    智能体流式运行的结果代理对象。
    提供高度解耦的方法来消费底层事件流，支持获取纯净文本或全部事件。
    """

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self.is_complete: bool = False
        self._result: AgentRunResult[OutputDataT] | None = None

    async def stream_events(self) -> "AsyncIterator[AgentStreamEvent]":
        """获取底层的所有原始事件（包含工具调用过程等）"""

        async for event in self._event_bus:
            if isinstance(event, AgentRunEnd):
                self._result = cast(AgentRunResult[OutputDataT], event.result)
                self.is_complete = True
            elif isinstance(event, AgentRunError):
                raise event.error.with_traceback(None) from None
            yield event

    async def stream_text(self, delta: bool = False) -> AsyncIterator[str]:
        """
        过滤大模型的输出文本。
        """
        full_text = ""
        async for _ in self.stream_events():
            pass

        if self._result is not None:
            output = self._result.output
            if isinstance(output, str):
                full_text = output
            else:
                if isinstance(output, BaseModel):
                    full_text = json.dumps(model_dump(output), ensure_ascii=False)
                else:
                    full_text = str(output)

            if delta:
                yield full_text
            else:
                yield full_text

    async def get_output(self) -> OutputDataT:
        """阻塞并等待整个 Agent 执行完毕，返回最终的解析输出数据"""
        if self._result is not None:
            return self._result.output

        async for _ in self.stream_events():
            pass

        if self._result is None:
            raise RuntimeError("Agent 运行异常结束，未产生最终结果。")

        return self._result.output

    async def get_run_result(self) -> AgentRunResult[OutputDataT]:
        """获取完整的运行结果对象（包含 Token 消耗等）"""
        if self._result is not None:
            return self._result

        await self.get_output()

        return cast(AgentRunResult[OutputDataT], self._result)

    async def forward_to(
        self, target_event_bus: EventBus | None, prefix_name: str
    ) -> AgentRunResult[OutputDataT]:
        """将底层的事件流自动格式化并转发给另一个事件发射器，
        常用于 DelegateTool 嵌套调用"""
        async for event in self.stream_events():
            if not target_event_bus:
                continue

            if isinstance(event, ToolStreamChunkEvent):
                await target_event_bus.emit(
                    ToolStreamChunkEvent(
                        tool_name=f"{prefix_name} -> {event.tool_name}",
                        content=event.content,
                        metadata=event.metadata,
                    )
                )
            elif isinstance(event, ToolCallStartEvent):
                intent_str = (
                    f" (意图: {event.intent})" if getattr(event, "intent", None) else ""
                )
                await target_event_bus.emit(
                    ToolStreamChunkEvent(
                        tool_name=prefix_name,
                        content=f"🔁 正在调用工具: {event.tool_name}...{intent_str}",
                    )
                )

        return await self.get_run_result()


class AgentTask(BaseModel):
    """标准化数据契约（意图载体 Payload），定义大模型需要做什么及产出什么格式"""

    id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex)
    """任务的唯一标识符"""

    name: str | None = None
    """任务的简短名称"""

    description: str
    """详细的任务指令（告诉大模型具体需要做什么）"""

    expected_output: str
    """预期输出的自然语言描述（指导大模型如何组织最终答案）"""

    response_model: type[BaseModel] | BaseOutputDefinition | None = None
    """强制要求返回的强类型结构 (Pydantic Model) 或
    OutputDefinition，为空则返回普通文本"""

    tools: list[str | Callable | dict[str, Any] | BaseTool | ToolResolvable] | None = (
        None
    )
    """针对此特定任务动态追加或覆盖的工具列表"""

    guardrails: list[GuardrailSource] | None = None
    """护栏验证列表。支持传入函数、BaseGuardrail 实例，
    或直接传入自然语言字符串规则（自动转为 LLM 裁判）"""

    _parsed_guardrails: list[BaseGuardrail] = PrivateAttr(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @model_validator(mode="after")
    def _parse_and_set_guardrails(self) -> AgentTask:
        from zhenxun.services.ai.guardrails import parse_guardrails

        self._parsed_guardrails = parse_guardrails(self.guardrails)
        return self


__all__ = [
    "AgentRunResult",
    "AgentTask",
    "OutputDataT",
    "StreamedRunResult",
]

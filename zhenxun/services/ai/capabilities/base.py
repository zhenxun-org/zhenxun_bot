from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Union

from zhenxun.services.ai.core.messages import ChatRequest, ChatResponse
from zhenxun.services.ai.core.models import LLMContext
from zhenxun.services.ai.core.options import GenerationConfig

if TYPE_CHECKING:
    from zhenxun.services.ai.run import AgentRunResult, RunContext

WrapRunHandler = Callable[[], Awaitable["AgentRunResult[Any]"]]
"""整个 Agent 运行过程包裹的处理函数类型"""

WrapModelRequestHandler = Callable[
    [LLMContext[ChatRequest, ChatResponse]], Awaitable[ChatResponse]
]
"""单次大模型 API 请求包裹的处理函数类型"""

WrapToolValidateHandler = Callable[[str | dict[str, Any]], Awaitable[dict[str, Any]]]
"""工具参数校验过程包裹的处理函数类型"""

WrapToolExecuteHandler = Callable[[dict[str, Any]], Awaitable[Any]]
"""单一工具执行过程包裹的处理函数类型"""


CapabilityPosition = Literal["outermost", "innermost"]
"""Capability 在洋葱模型中的固定执行位置（最外层或最内层）"""

CapabilityRef = Union[type["AbstractCapability"], "AbstractCapability"]
"""对 Capability 的引用，可以是 Capability 实例或类类型"""


@dataclass
class CapabilityOrdering:
    """定义拦截器 (Capability) 的拓扑排序约束。
    采用洋葱模型语义：排在列表前面的拦截器在最外层执行。
    """

    position: CapabilityPosition | None = None
    """固定位置：outermost (最外层) 或 innermost (最内层)"""
    wraps: Sequence[CapabilityRef] = ()
    """当前拦截器必须包裹（即在...之前执行）目标拦截器"""
    wrapped_by: Sequence[CapabilityRef] = ()
    """当前拦截器必须被包裹（即在...之后执行）目标拦截器"""
    requires: Sequence[type["AbstractCapability"]] = ()
    """当前拦截器依赖的其他拦截器类型，若缺失则报错"""


class AbstractCapability:
    """
    Agent 能力组件基类协议。
    """

    def get_ordering(self) -> CapabilityOrdering | None:
        """获取该拦截器的拓扑排序约束。子类可重写此方法以锁定执行顺序。"""
        return None

    async def for_run(self, context: RunContext) -> "AbstractCapability":
        """获取专用于单次运行的实例,默认返回自身(无状态)。"""
        return self

    async def get_generation_config(
        self, context: RunContext
    ) -> GenerationConfig | None:
        """运行开始前触发。允许动态下发大模型配置（覆盖或合并 Agent 的默认配置）。"""
        return None

    async def get_system_prompts(self, context: RunContext) -> list[str]:
        """获取该能力提供的系统提示词列表。"""
        return []

    async def get_tools(self, context: RunContext) -> list[Any]:
        """获取该能力附带的工具列表。"""
        return []

    async def prepare_tools(
        self, context: RunContext, tool_defs: list[Any]
    ) -> list[Any]:
        """运行开始前/装配工具时触发。允许动态增删改当前将发往大模型的工具列表。
        默认实现：无操作，直接返回传入的工具列表。"""
        return tool_defs

    async def wrap_run(
        self, context: RunContext, handler: WrapRunHandler
    ) -> "AgentRunResult[Any]":
        """包裹整个 Agent 运行过程 (洋葱模型)。"""
        return await handler()

    async def wrap_model_request(
        self,
        context: RunContext,
        llm_context: LLMContext[ChatRequest, ChatResponse],
        handler: WrapModelRequestHandler,
    ) -> ChatResponse:
        """包裹单次大模型 API 请求 (洋葱模型)。"""
        return await handler(llm_context)

    async def wrap_tool_validate(
        self,
        context: RunContext,
        tool_name: str,
        args: str | dict[str, Any],
        handler: WrapToolValidateHandler,
    ) -> dict[str, Any]:
        """包裹工具的参数校验过程 (洋葱模型)。"""
        return await handler(args)

    async def wrap_tool_execute(
        self,
        context: RunContext,
        tool_name: str,
        arguments: dict[str, Any],
        handler: WrapToolExecuteHandler,
    ) -> Any:
        """包裹单一工具的执行 (洋葱模型)。"""
        return await handler(arguments)

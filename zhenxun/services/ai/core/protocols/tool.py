"""
工具执行与管理协议定义
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from zhenxun.services.ai.core.models import ToolDefinition

if TYPE_CHECKING:
    from zhenxun.services.ai.run import RunContext
    from zhenxun.services.ai.tools.models import (
        ResolvedToolPayload,
        ToolResult,
    )


class ToolExecutable(Protocol):
    """
    一个协议，定义了所有可被LLM调用的工具必须实现的行为。
    """

    name: str
    """工具的名称标识"""

    async def get_definition(
        self, context: "RunContext | None" = None
    ) -> ToolDefinition | None:
        """
        异步地获取一个结构化的工具定义。如果返回 None，则该工具对大模型不可见。
        """
        ...

    async def execute(
        self, context: "RunContext | None" = None, **kwargs: Any
    ) -> ToolResult:
        """
        异步执行工具并返回一个结构化的结果。
        """
        ...




@runtime_checkable
class ToolResolvable(Protocol):
    """
    鸭子类型解析协议。任何实现了此协议的对象，
    都可以直接被 Agent 或 LLM 的 tools 参数接收。
    """

    async def resolve(
        self, context: "RunContext | None" = None
    ) -> "ResolvedToolPayload": ...


class ToolProvider(Protocol):
    """
    一个协议，定义了"工具提供者"的行为。
    工具提供者负责发现或实例化具体的 ToolExecutable 对象。
    """

    async def initialize(self) -> None:
        """
        异步初始化提供者。
        """
        ...

    async def discover_tools(
        self,
        allowed_servers: list[str] | None = None,
        excluded_servers: list[str] | None = None,
    ) -> dict[str, ToolExecutable]:
        """
        异步发现此提供者提供的所有工具。
        """
        ...

    async def get_tool_executable(
        self, name: str, config: dict[str, Any]
    ) -> ToolExecutable | None:
        """
        如果此提供者能处理名为 'name' 的工具，则返回一个可执行实例。
        """
        ...

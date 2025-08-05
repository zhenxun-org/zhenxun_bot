"""
LLM 模块的协议定义
"""

from typing import Any, Protocol

from .models import ToolDefinition, ToolResult


class ToolExecutable(Protocol):
    """
    一个协议，定义了所有可被LLM调用的工具必须实现的行为。
    它将工具的"定义"（给LLM看）和"执行"（由框架调用）封装在一起。
    """

    async def get_definition(self) -> ToolDefinition:
        """
        异步地获取一个结构化的工具定义。
        """
        ...

    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        异步执行工具并返回一个结构化的结果。
        参数由LLM根据工具定义生成。
        """
        ...


class ToolProvider(Protocol):
    """
    一个协议，定义了"工具提供者"的行为。
    工具提供者负责发现或实例化具体的 ToolExecutable 对象。
    """

    async def initialize(self) -> None:
        """
        异步初始化提供者。
        此方法应是幂等的，即多次调用只会执行一次初始化逻辑。
        用于执行耗时的I/O操作，如网络请求或启动子进程。
        """
        ...

    async def discover_tools(
        self,
        allowed_servers: list[str] | None = None,
        excluded_servers: list[str] | None = None,
    ) -> dict[str, ToolExecutable]:
        """
        异步发现此提供者提供的所有工具。
        在 `initialize` 成功调用后才应被调用。

        返回:
            一个从工具名称到 ToolExecutable 实例的字典。
        """
        ...

    async def get_tool_executable(
        self, name: str, config: dict[str, Any]
    ) -> ToolExecutable | None:
        """
        【保留】如果此提供者能处理名为 'name' 的工具，则返回一个可执行实例。
        此方法主要用于按需解析 ad-hoc 工具。
        """
        ...

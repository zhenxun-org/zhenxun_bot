"""
LLM 模块的协议定义
"""

from typing import Any, Protocol


class MCPCompatible(Protocol):
    """
    一个协议，定义了与LLM模块兼容的MCP会话对象应具备的行为。
    任何实现了 to_api_tool 方法的对象都可以被认为是 MCPCompatible。
    """

    def to_api_tool(self, api_type: str) -> dict[str, Any]:
        """
        将此MCP会话转换为特定LLM提供商API所需的工具格式。

        参数:
            api_type: 目标API的类型 (例如 'gemini', 'openai')。

        返回:
            dict[str, Any]: 一个字典，代表可以在API请求中使用的工具定义。
        """
        ...

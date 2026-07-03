import nonebot

from .bridges.matcher_bridge import bind_matcher
from .core.decorators import Rules, tool, toolkit
from .core.toolkit import BaseToolkit
from .engine.registry import tool_provider_manager
from .models import (
    ToolOptions,
    ToolResult,
)
from .providers.builtin.native import Native
from .providers.mcp.provider import mcp_provider

tool_provider_manager.register(mcp_provider)


@nonebot.get_driver().on_shutdown
async def _shutdown_mcp_provider():
    """在服务关闭时停止所有 MCP 子进程服务器"""
    await mcp_provider.shutdown()


__all__ = [
    "BaseToolkit",
    "Native",
    "Rules",
    "ToolOptions",
    "ToolResult",
    "bind_matcher",
    "tool",
    "toolkit",
]

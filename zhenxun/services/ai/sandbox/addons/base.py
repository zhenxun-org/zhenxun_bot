from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from zhenxun.services.log import logger

if TYPE_CHECKING:
    from zhenxun.services.ai.sandbox.drivers.base import BaseSandboxSession


class BaseSandboxExtension(ABC):
    """沙箱功能能力扩展基类"""

    def __init__(self, session: "BaseSandboxSession"):
        """初始化沙箱扩展实例，绑定当前沙箱会话"""
        self.session = session

    @property
    @abstractmethod
    def extension_name(self) -> str:
        """获取扩展能力的唯一名称"""
        pass

    async def on_mount(self) -> None:
        """在扩展挂载到会话时触发的钩子函数"""
        logger.debug(f"[SandboxExtension] 扩展 '{self.extension_name}' 已挂载。")

    async def on_unmount(self) -> None:
        """在扩展从会话卸载时触发的钩子函数"""
        logger.debug(f"[SandboxExtension] 扩展 '{self.extension_name}' 已卸载。")


class BaseMcpProxyExtension(BaseSandboxExtension):
    """沙箱 MCP 代理扩展基类"""

    @abstractmethod
    @asynccontextmanager
    async def connect_mcp(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> AsyncGenerator[tuple[Any, Any], None]:
        """建立与沙箱内部 MCP 服务的代理连接并返回输入输出管道"""
        yield None, None

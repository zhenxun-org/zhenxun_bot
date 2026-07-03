from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import json
from typing import Any

from anyio import create_memory_object_stream, create_task_group
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from zhenxun.services.ai.sandbox.addons.base import BaseMcpProxyExtension
from zhenxun.services.ai.sandbox.protocols import SupportsStreamExecution
from zhenxun.services.ai.sandbox.registry import SandboxRegistry
from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_dump_json, model_validate


class UniversalMcpExtension(BaseMcpProxyExtension):
    """通用 MCP 代理扩展类，用于在沙箱内连接 MCP 服务"""

    @property
    def extension_name(self) -> str:
        """获取 MCP 代理扩展的唯一名称"""
        return "universal_mcp"

    @asynccontextmanager
    async def connect_mcp(
        self, command: str, args: list[str], env: dict[str, str] | None = None
    ) -> AsyncGenerator[tuple[Any, Any], None]:
        """启动沙箱内的 MCP 服务器，并建立与之进行 JSON-RPC 通信的双向内存流管道"""
        if not isinstance(self.session, SupportsStreamExecution):
            raise RuntimeError(
                "当前沙箱驱动不支持流式后台进程执行 (SupportsStreamExecution)，"
                "无法启动原生 MCP 代理。"
            )

        logger.info(
            "[UniversalMcpExtension] 正在沙箱内原生启动 MCP 服务器: "
            f"{command} {' '.join(args)}"
        )

        cmd_list = [command, *args]

        async with self.session.create_stream_process(
            command=cmd_list, cwd=self.session.workspace_path, env=env
        ) as process_stream:
            read_prod, read_cons = create_memory_object_stream(10)
            write_prod, write_cons = create_memory_object_stream(10)

            async def stream_reader():
                buffer = b""
                try:
                    while True:
                        msg = await process_stream.read()
                        if msg is None:
                            break
                        if msg.stream_type == 1:
                            buffer += msg.data
                            while b"\n" in buffer:
                                line, buffer = buffer.split(b"\n", 1)
                                if not line.strip():
                                    continue
                                try:
                                    msg = model_validate(
                                        JSONRPCMessage, json.loads(line)
                                    )
                                    await read_prod.send(SessionMessage(message=msg))
                                except Exception as exc:
                                    await read_prod.send(exc)
                except Exception:
                    pass
                finally:
                    await read_prod.aclose()

            async def stream_writer():
                try:
                    async for msg in write_cons:
                        data = (
                            model_dump_json(
                                msg.message, by_alias=True, exclude_none=True
                            ).encode("utf-8")
                            + b"\n"
                        )
                        await process_stream.write(data)
                except Exception:
                    pass

            async with create_task_group() as tg:
                tg.start_soon(stream_reader)
                tg.start_soon(stream_writer)
                yield read_cons, write_prod
                tg.cancel_scope.cancel()


SandboxRegistry.register_extension(UniversalMcpExtension)

__all__ = [
    "UniversalMcpExtension",
]

import asyncio
import base64
from collections.abc import AsyncGenerator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
import re
from typing import Any, Literal, cast

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
import httpx
from mcp import ClientSession  # type: ignore
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

from zhenxun.services.ai.core.models import ToolDefinition
from zhenxun.services.ai.core.stream_events import UserCustomEvent
from zhenxun.services.ai.run.context import RunContext
from zhenxun.services.ai.sandbox.addons.base import BaseMcpProxyExtension
from zhenxun.services.ai.sandbox.models import SandboxBlueprint
from zhenxun.services.ai.tools.core.tool import BaseTool
from zhenxun.services.ai.tools.core.toolkit import BaseToolkit
from zhenxun.services.ai.tools.models import ToolkitConfig, ToolResult
from zhenxun.services.ai.utils.logger import log_tool as logger
from zhenxun.utils.lifespan import LifespanManager
from zhenxun.utils.pydantic_compat import model_dump

_MESSAGE_START_CHARS = {"{", "["}
_LITERAL_PREFIXES: tuple[str, ...] = ("true", "false", "null")


def _should_ignore_exception(exc: Exception) -> bool:
    """
    判断该异常是否是由非 JSON 的脏数据标准输出引起的。
    如果是脏数据，则可以安全忽略。
    """
    if not isinstance(exc, ValidationError):
        return False

    errors = exc.errors()
    first = next(iter(errors), None)
    if not first or first.get("type") != "json_invalid":
        return False

    input_value = first.get("input")
    if not isinstance(input_value, str):
        return False

    stripped = input_value.strip()
    if not stripped:
        return True

    first_char = stripped[0]
    lowered = stripped.lower()

    if first_char in _MESSAGE_START_CHARS or any(
        lowered.startswith(prefix) for prefix in _LITERAL_PREFIXES
    ):
        return False

    return True


@asynccontextmanager
async def filtered_stdio_client(
    server_name: str, server: StdioServerParameters
) -> AsyncGenerator[
    tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
    ],
    None,
]:
    """
    包裹官方的 stdio_client，拦截并过滤掉非 JSON 格式的标准输出噪音。
    """
    async with stdio_client(server=server) as (read_stream, write_stream):
        filtered_send, filtered_recv = anyio.create_memory_object_stream[
            SessionMessage | Exception
        ](0)

        async def _forward_stdout() -> None:
            try:
                async with read_stream:
                    async for item in read_stream:
                        if isinstance(item, Exception) and _should_ignore_exception(
                            item
                        ):
                            if isinstance(item, ValidationError):
                                err_input = item.errors()[0].get("input", "")
                                logger.debug(
                                    f"🔇 [MCP Stdout Filter] {server_name} 忽略脏数据: "
                                    f"{str(err_input)[:100].strip()}..."
                                )
                            continue
                        await filtered_send.send(item)
            except anyio.ClosedResourceError:
                pass
            finally:
                await filtered_send.aclose()

        async with anyio.create_task_group() as tg:
            tg.start_soon(_forward_stdout)
            try:
                yield filtered_recv, write_stream
            finally:
                tg.cancel_scope.cancel()


class MCPRemoteTool(BaseTool):
    """远端 MCP 工具在本地的代理对象"""

    def __init__(
        self,
        name: str,
        original_tool_name: str,
        description: str,
        parameters: dict,
        toolkit: "MCPToolkit",
    ):
        """
        初始化远端 MCP 工具在本地的代理对象。

        参数:
            name: 工具在本地注册的唯一标识名称。
            original_tool_name: 该工具在远端 MCP 服务器上的原始名称。
            description: 工具的描述信息，用于指导大模型选用此工具。
            parameters: 工具参数的 JSON 模式描述。
            toolkit: 所属的 MCPToolkit 工具箱实例。
        """
        super().__init__(name=name, description=description)
        self.original_tool_name = original_tool_name
        self.parameters = parameters
        self.toolkit = toolkit
        self.parent_toolkit = toolkit
        self.args_schema = None
        self.metadata = (
            {"admin_level": toolkit.admin_level} if toolkit.admin_level > 0 else {}
        )

    @property
    def effective_ttl(self) -> float:
        """获取当前工具关联的有效生存期 TTL"""
        try:
            from zhenxun.services.ai.config import get_llm_config

            mcp_ttl = get_llm_config().agent_settings.mcp_cleanup_timeout
            return 31536000.0 if mcp_ttl <= 0 else float(mcp_ttl)
        except Exception:
            return 31536000.0 if self.toolkit.ttl <= 0 else float(self.toolkit.ttl)

    async def get_definition(
        self, context: RunContext | None = None
    ) -> ToolDefinition | None:
        if hasattr(self, "_dynamic_def") and self._dynamic_def is not None:
            return self._dynamic_def
        tool_def = ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            metadata=self.metadata or {},
        )
        if context and self.settings.capabilities:
            from zhenxun.services.ai.capabilities import (
                CombinedCapability,
            )

            combined_cap = CombinedCapability(self.settings.capabilities)
            defs = await combined_cap.prepare_tools(context, [tool_def])
            if not defs:
                return None
            tool_def = defs[0]
        return tool_def

    async def execute(self, context: RunContext | None = None, **kwargs) -> ToolResult:
        max_retries = 3
        last_error = None

        for attempt in range(max_retries):
            await self.toolkit.lifespan_manager.touch(
                self.toolkit.server_name, self.toolkit.effective_ttl
            )

            session = await self.toolkit.get_session(context)
            if not session:
                return ToolResult(output="MCP 连接错误").as_error()

            try:
                result = await session.call_tool(self.original_tool_name, kwargs)
                break
            except Exception as e:
                last_error = e
                logger.warning(
                    f"MCP 工具 '{self.name}' 执行失败 "
                    f"(尝试 {attempt + 1}/{max_retries}): {e}。"
                    "正在尝试自动重连自愈..."
                )
                if attempt < max_retries - 1:
                    await self.toolkit.close()
                    await asyncio.sleep(2**attempt)
        else:
            logger.error(
                f"MCP 工具 '{self.name}' "
                f"在重试 {max_retries} 次后仍然执行失败: {last_error}"
            )
            return ToolResult(output=f"MCP 错误: {last_error}").as_error()

        if result.isError:
            return ToolResult(output=str(result.content)).as_error()

        from zhenxun.services.ai.core.messages import ImagePart, TextPart

        output_content = []
        img_count = 0

        for item in result.content:
            item_type = getattr(item, "type", "text")

            if item_type == "image":
                b64_data = getattr(item, "data", "")
                mime_type = getattr(item, "mimeType", "image/png")
                if b64_data:
                    try:
                        img_bytes = base64.b64decode(b64_data)
                        output_content.append(
                            ImagePart(raw=img_bytes, mime_type=mime_type)
                        )
                        img_count += 1
                        continue
                    except Exception as e:
                        logger.warning(f"MCP 图片 Base64 解码失败: {e}")

                output_content.append(TextPart(text="[图片解码失败]"))

            elif item_type == "text":
                text = getattr(item, "text", str(item))
                output_content.append(TextPart(text=text))

                md_images = re.findall(r"!\[.*?\]\((https?://[^\)]+)\)", text)
                for img_url in md_images:
                    output_content.append(ImagePart(url=img_url))
                    img_count += 1
            else:
                dumped = model_dump(item) if hasattr(item, "model_dump") else str(item)
                output_content.append(TextPart(text=str(dumped)))

        tool_result = ToolResult(output=output_content)
        logger.info(
            f"获取到 {len(output_content)} 条返回数据，提取了 {img_count} 张图片"
        )

        if img_count > 0 and context and context.run.event_bus:
            await context.run.event_bus.emit(UserCustomEvent(display=output_content))

        return tool_result


class MCPToolkit(BaseToolkit):
    """模型上下文协议 (MCP) 的工具箱封装 (支持声明式挂载与动态隔离)"""

    def __init__(
        self,
        server_name: str,
        prefix: str | None = None,
        transport: Literal[
            "stdio", "sse", "streamable-http", "sandbox_proxy"
        ] = "stdio",
        command: str | None = None,
        args: list[str] | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        env: dict | None = None,
        cwd: str | None = None,
        install_command: str | None = None,
        timeout: int = 30,
        admin_level: int = 0,
        header_provider: Callable[[RunContext], dict[str, str]] | None = None,
        env_provider: Callable[[RunContext], dict[str, str]] | None = None,
        ttl: int = 600,
        sandbox_session_id: str | None = None,
        sandbox_blueprint: SandboxBlueprint | None = None,
    ):
        """
        初始化模型上下文协议 (MCP) 的工具箱封装。

        参数:
            server_name: MCP 服务器的唯一标识名称。
            prefix: 工具名称的前缀，防止工具命名冲突。
            transport: 连接 MCP 服务器的通信传输协议，可选 "stdio", "sse", "streamable-http", "sandbox_proxy"。
            command: 用于 stdio 或 sandbox_proxy 模式启动服务器的可执行命令。
            args: 启动服务器时附加的命令行参数。
            url: 用于 sse 或 streamable-http 模式的服务器连接 URL。
            headers: 静态配置的 HTTP 请求头。
            env: 启动服务器时的进程环境变量。
            cwd: 启动服务器时的进程工作目录。
            install_command: 首次启动前执行的环境热装配/依赖安装命令。
            timeout: 初始化和请求网络接口时的超时秒数，默认 30。
            admin_level: 调用工具所需的管理权限等级，默认 0 (无限制)。
            header_provider: 动态生成 HTTP 头部信息的工厂函数。
            env_provider: 动态生成进程环境变量的工厂函数。
            ttl: 闲置清理的生存时间 (秒)，默认 600。
            sandbox_session_id: 当 transport 为 sandbox_proxy 时指定的沙箱会话 ID。
            sandbox_blueprint: 用于沙箱自动装配的环境蓝图配置。
        """  # noqa: E501
        super().__init__(
            config=ToolkitConfig(prefix=prefix) if prefix is not None else None
        )
        self.server_name = server_name
        self.transport = transport
        self.command = command
        self.args = args or []
        self.url = url
        self.headers = headers or {}
        self.env = env or {}
        self.cwd = cwd
        self.install_command = install_command
        self.timeout = timeout
        self.admin_level = admin_level
        self.header_provider = header_provider
        self.env_provider = env_provider
        self.sandbox_session_id = sandbox_session_id
        self.sandbox_blueprint = sandbox_blueprint
        self.ttl = ttl

        self._shared_session: ClientSession | None = None
        self._tools: list[BaseTool] = []
        self._is_initialized = False

        self.lifespan_manager = LifespanManager()

        self._shared_task: asyncio.Task | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._ready_event: asyncio.Event = asyncio.Event()
        self._init_exception: Exception | None = None

    @property
    def effective_ttl(self) -> float:
        """获取当前工具箱的有效闲置生存期 TTL"""
        try:
            from zhenxun.services.ai.config import get_llm_config

            mcp_ttl = get_llm_config().agent_settings.mcp_cleanup_timeout
            return 31536000.0 if mcp_ttl <= 0 else float(mcp_ttl)
        except Exception:
            return 31536000.0 if self.ttl <= 0 else float(self.ttl)

    async def _run_install_command(self, force: bool = False):
        """在本地运行预热依赖安装命令以准备 MCP 服务器环境"""
        if not self.install_command or not self.cwd:
            return

        from pathlib import Path

        marker_file = Path(self.cwd) / ".zx_installed"

        heuristic_missing = False
        if (
            "npm" in self.install_command
            and not (Path(self.cwd) / "node_modules").exists()
        ):
            heuristic_missing = True

        if not force and not heuristic_missing and marker_file.exists():
            return

        reason = "强制触发自愈重装" if force else "首次启动或检测到环境缺失"
        logger.info(
            f"🔧 [{self.server_name}] {reason}，正在执行: `{self.install_command}` ..."
        )

        try:
            proc = await asyncio.create_subprocess_shell(
                self.install_command,
                cwd=self.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                err_msg = (
                    stderr.decode("utf-8", errors="ignore")
                    if stderr
                    else stdout.decode("utf-8", errors="ignore")
                )
                logger.error(
                    f"❌ [{self.server_name}] 环境安装失败!\n错误输出:\n{err_msg}"
                )
                raise RuntimeError(f"安装命令执行失败: {self.install_command}")
            else:
                marker_file.touch()
                logger.info(f"✅ [{self.server_name}] 环境装配成功！")
        except Exception as e:
            logger.error(f"执行预热安装异常: {e}")
            raise e

    async def get_session(
        self, context: RunContext | None = None
    ) -> ClientSession | None:
        """获取或建立与 MCP 服务器的会话连接，并刷新其生存状态"""
        await self.lifespan_manager.register(
            self.server_name,
            ttl=self.effective_ttl,
            cleanup_callback=self.release_resource,
        )
        if not self._is_initialized:
            await self.initialize(context)
        return self._shared_session

    async def _spawn_session_task(self, dynamic_headers: dict, dynamic_env: dict):
        """在后台异步拉起 MCP 传输协议通道并进行服务初始化"""
        max_attempts = 2 if (self.transport == "stdio" and self.install_command) else 1

        try:
            for attempt in range(1, max_attempts + 1):
                try:
                    if self.transport == "stdio":
                        await self._run_install_command(force=(attempt > 1))

                    async with AsyncExitStack() as stack:
                        read_stream: Any = None
                        write_stream: Any = None
                        if self.transport == "stdio":
                            if not self.command:
                                raise ValueError("stdio requires 'command'")

                            params = StdioServerParameters(
                                command=self.command,
                                args=self.args,
                                env=dynamic_env,
                                cwd=self.cwd,
                            )
                            transport_ctx = filtered_stdio_client(
                                self.server_name, params
                            )
                        elif self.transport == "sse":
                            if not self.url:
                                raise ValueError("sse requires 'url'")
                            transport_ctx = sse_client(
                                url=self.url,
                                headers=dynamic_headers,
                                timeout=self.timeout,
                            )
                        elif self.transport == "streamable-http":
                            if not self.url:
                                raise ValueError("streamable-http requires 'url'")

                            http_client = httpx.AsyncClient(
                                headers=dynamic_headers, timeout=self.timeout
                            )
                            await stack.enter_async_context(http_client)
                            transport_ctx = streamable_http_client(
                                url=self.url, http_client=http_client
                            )
                        elif self.transport == "sandbox_proxy":
                            from zhenxun.services.ai.sandbox.manager import (
                                sandbox_manager,
                            )

                            target_session_id = (
                                self.sandbox_session_id or "mcp_global_session"
                            )
                            bp = self.sandbox_blueprint or SandboxBlueprint()
                            bp.enable_network = True

                            driver = await sandbox_manager.get_or_create_session(
                                target_session_id,
                                blueprint=bp,
                            )

                            plugin_name = "universal_mcp"

                            await driver.mount_extension(plugin_name)
                            mcp_plugin = cast(
                                BaseMcpProxyExtension, driver.get_extension(plugin_name)
                            )

                            if not self.command:
                                raise ValueError("sandbox_proxy requires 'command'")

                            streams = await stack.enter_async_context(
                                mcp_plugin.connect_mcp(
                                    self.command, self.args, dynamic_env
                                )
                            )
                            read_stream, write_stream = streams[0], streams[1]
                            transport_ctx = None
                        else:
                            raise ValueError(f"Unknown transport: {self.transport}")

                        if transport_ctx:
                            transport = await stack.enter_async_context(transport_ctx)
                            read_stream, write_stream = transport[0], transport[1]

                        client_session = await stack.enter_async_context(
                            ClientSession(read_stream, write_stream)
                        )
                        await client_session.initialize()

                        self._shared_session = client_session

                        if not self._is_initialized:
                            mcp_tools_res = await client_session.list_tools()
                            tools_list = list(mcp_tools_res.tools)
                            cursor = getattr(mcp_tools_res, "nextCursor", None)

                            while cursor:
                                mcp_tools_res = await client_session.list_tools(
                                    cursor=cursor
                                )
                                tools_list.extend(mcp_tools_res.tools)
                                cursor = getattr(mcp_tools_res, "nextCursor", None)

                            for t in tools_list:
                                t_name = t.name.replace("-", "_")
                                final_name = (
                                    f"{self.config.prefix}{t_name}"
                                    if self.config.prefix
                                    else t_name
                                )
                                self._tools.append(
                                    MCPRemoteTool(
                                        name=final_name,
                                        original_tool_name=t.name,
                                        description=t.description or "",
                                        parameters=t.inputSchema,
                                        toolkit=self,
                                    )
                                )
                            self._is_initialized = True

                        self._ready_event.set()
                        logger.info(
                            f"成功连接 MCP 服务器: {self.server_name}, "
                            f"获取了 {len(self._tools)} 个工具"
                        )

                        await self._stop_event.wait()
                        return

                except Exception as e:
                    from zhenxun.services.ai.core.exceptions import SandboxFatalError

                    if isinstance(e, SandboxFatalError):
                        raise e

                    if attempt < max_attempts:
                        logger.warning(
                            f"⚠️ [{self.server_name}] 进程启动或运行异常崩溃，"
                            "疑似环境损坏。触发自愈机制 (准备重试)..."
                        )
                        self._init_exception = e
                        self._shared_session = None
                        if not self._is_initialized:
                            self._is_initialized = False
                        continue

                    raise e

        except Exception as e:
            self._init_exception = e
            logger.error(
                f"MCP 服务器 '{self.server_name}' "
                f"初始化连接失败（模式: {self.transport}）。"
                f"错误原因: {e}"
            )
            if "Connection closed" in str(e):
                logger.error(
                    "提示：若是使用沙箱隧道，请进入 Docker Desktop 检查容器的日志。"
                    "多半是因为 npx 命令执行出错"
                    "（例如：镜像中缺少 Node 环境或国内网络无法连接 npm 等）。"
                )
        finally:
            if not self._is_initialized:
                self._is_initialized = False
            self._shared_session = None
            self._ready_event.set()

    async def initialize(self, context: RunContext | None = None):
        """清空当前工具集并异步执行 MCP 服务端的全套初始化流程"""
        if self._is_initialized:
            return

        self._tools.clear()
        self._stop_event.clear()
        self._ready_event.clear()
        self._init_exception = None

        dynamic_headers = self.headers.copy()
        if self.header_provider and context:
            dynamic_headers.update(self.header_provider(context))

        dynamic_env = self.env.copy()
        if self.env_provider and context:
            dynamic_env.update(self.env_provider(context))

        self._shared_task = asyncio.create_task(
            self._spawn_session_task(dynamic_headers, dynamic_env)
        )

        await self._ready_event.wait()

        if self._init_exception:
            raise self._init_exception

    async def get_tools(self, context: RunContext | None = None) -> dict[str, BaseTool]:
        await self.lifespan_manager.register(
            self.server_name,
            ttl=self.effective_ttl,
            cleanup_callback=self.release_resource,
        )
        if not self._is_initialized:
            await self.initialize(context)

        tools_dict = {}
        for t in self._tools:
            tools_dict[t.name] = t
        return tools_dict

    async def release_resource(self, resource_id: str):
        await self.close()

    async def close(self):
        """彻底关闭 MCP 工具箱，注销生存期并释放底层 WebSocket/进程通道"""
        current_task = asyncio.current_task()

        if (
            self.lifespan_manager._watchdog_task
            and self.lifespan_manager._watchdog_task is not current_task
        ):
            await self.lifespan_manager.stop()

        self._stop_event.set()
        self._is_initialized = False
        self._shared_session = None
        self._tools.clear()

        if (
            self._shared_task
            and not self._shared_task.done()
            and self._shared_task is not current_task
        ):
            try:
                await asyncio.wait_for(self._shared_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    f"MCP 服务器 '{self.server_name}' 的任务执行超时，正在取消。"
                )
                self._shared_task.cancel()
        self._shared_task = None

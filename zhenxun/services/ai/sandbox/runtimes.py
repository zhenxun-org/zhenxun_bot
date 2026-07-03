from abc import ABC, abstractmethod
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ClassVar
import uuid

import aiohttp

from zhenxun.services.ai.sandbox.drivers.base import BaseSandboxSession
from zhenxun.services.ai.sandbox.models import (
    LanguageProfile,
    SandboxExecutionResult,
)
from zhenxun.services.log import logger
from zhenxun.utils.utils import infer_plugin_namespace


def parse_shebang(script_path: str | Path) -> str | None:
    """解析脚本首行的 Shebang，获取解释器名称"""
    path = Path(script_path)
    if not path.is_file():
        return None

    try:
        with open(path, encoding="utf-8") as f:
            first_line = f.readline().strip()

        if not first_line.startswith("#!"):
            return None

        shebang = first_line[2:].strip()
        if not shebang:
            return None

        parts = shebang.split()
        if Path(parts[0]).name == "env":
            for part in parts[1:]:
                if not part.startswith("-"):
                    return part
            return None
        return Path(parts[0]).name
    except Exception:
        return None


def get_execution_command(
    script_path: str | Path, args: list[str] | None = None
) -> str:
    """根据 Shebang 或文件后缀生成对应的脚本执行命令"""
    path = Path(script_path)
    interpreter = parse_shebang(path)
    if not interpreter:
        ext = path.suffix.lower()
        if ext == ".py":
            interpreter = "python3"
        elif ext in (".js", ".ts"):
            interpreter = "node"
        elif ext in (".sh", ".bash"):
            interpreter = "bash"
        else:
            interpreter = "sh"

    cmd_parts = [interpreter, path.as_posix()]
    if args:
        cmd_parts.extend(args)
    return " ".join(cmd_parts)


class JupyterWSClient:
    """管理与单个 Jupyter Kernel WebSocket 通道通信的客户端"""

    def __init__(
        self,
        http_session: aiohttp.ClientSession,
        base_url: str,
        ws_url: str,
        kernel_id: str,
    ):
        """初始化 Jupyter WebSocket 客户端并绑定内核ID"""
        self.http_session = http_session
        self.base_url = base_url
        self.ws_url = ws_url
        self.kernel_id = kernel_id
        self.ws: aiohttp.ClientWebSocketResponse | None = None

    async def _connect_ws(self) -> aiohttp.ClientWebSocketResponse:
        """建立并返回与 Jupyter Kernel 活跃的 WebSocket 连接"""
        if self.ws is None or self.ws.closed:
            self.ws = await self.http_session.ws_connect(
                f"{self.ws_url}/api/kernels/{self.kernel_id}/channels"
            )
        assert self.ws is not None
        return self.ws

    async def interrupt(self):
        """向 Jupyter Kernel 发送 HTTP POST 中断正在执行的进程"""
        try:
            async with self.http_session.post(
                f"{self.base_url}/api/kernels/{self.kernel_id}/interrupt"
            ):
                pass
            logger.debug(
                f"[JupyterWSClient] 成功向 Kernel {self.kernel_id} 发送中断信号"
            )
        except Exception as e:
            logger.warning(f"[JupyterWSClient] 中断 Kernel 失败: {e}")

    async def execute(self, code: str, timeout: int = 30, on_output=None):
        """在 Jupyter 核心中执行代码，并通过 WebSocket 接收标准输出、标准错误和图像"""
        background_tasks: set[asyncio.Task[None]] = set()

        try:
            ws = await self._connect_ws()
        except Exception as e:
            return SandboxExecutionResult(
                exit_code=-1, error=f"网络连接 Jupyter Kernel 失败: {e}"
            )

        msg_id = uuid.uuid4().hex
        req = {
            "header": {
                "msg_id": msg_id,
                "msg_type": "execute_request",
                "version": "5.0",
            },
            "parent_header": {},
            "metadata": {},
            "channel": "shell",
            "content": {"code": code, "silent": False, "store_history": False},
        }

        try:
            if ws.closed:
                ws = await self._connect_ws()
            await ws.send_json(req)
        except Exception as e:
            logger.warning(f"[JupyterWSClient] WebSocket 发送失败，尝试重连: {e}")
            self.ws = None
            ws = await self._connect_ws()
            await ws.send_json(req)

        stdout_parts = []
        stderr_parts = []
        images = []
        exit_code = 0
        current_out_len = 0
        MAX_OUT_LEN = 50000

        async def _receive_loop():
            nonlocal exit_code, current_out_len
            while True:
                msg = await ws.receive_json()
                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue

                msg_type = msg["msg_type"]
                content = msg["content"]

                def _append_text(text: str, is_stdout: bool = True) -> bool:
                    nonlocal current_out_len
                    if current_out_len + len(text) > MAX_OUT_LEN:
                        allowed_len = max(0, MAX_OUT_LEN - current_out_len)
                        truncated = (
                            text[:allowed_len]
                            + "\n...[系统警告：输出超长已被强行截断]..."
                        )
                        (stdout_parts if is_stdout else stderr_parts).append(truncated)
                        current_out_len += len(truncated)
                        return False
                    current_out_len += len(text)
                    (stdout_parts if is_stdout else stderr_parts).append(text)
                    if on_output:
                        t = asyncio.create_task(
                            on_output(
                                "stdout" if is_stdout else "stderr",
                                text.encode("utf-8"),
                            )
                        )
                        background_tasks.add(t)
                        t.add_done_callback(background_tasks.discard)
                    return True

                if msg_type == "stream":
                    if not _append_text(
                        content["text"], is_stdout=(content["name"] == "stdout")
                    ):
                        task = asyncio.create_task(self.interrupt())
                        background_tasks.add(task)
                        task.add_done_callback(background_tasks.discard)
                        exit_code = -1
                        break
                elif msg_type == "error":
                    if not _append_text(
                        "\n".join(content["traceback"]), is_stdout=False
                    ):
                        break
                    exit_code = 1
                elif msg_type in ["display_data", "execute_result"]:
                    data = content.get("data", {})
                    if "text/plain" in data:
                        if not _append_text(data["text/plain"] + "\n", is_stdout=True):
                            break
                    if "image/png" in data:
                        images.append(data["image/png"])
                elif msg_type == "execute_reply":
                    if content.get("status") == "error":
                        exit_code = 1
                    break

        try:
            await asyncio.wait_for(_receive_loop(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            await self.interrupt()
            return SandboxExecutionResult(
                stdout="".join(stdout_parts),
                stderr="".join(stderr_parts),
                exit_code=-1,
                error=f"执行超时 ({timeout}s)",
            )
        except Exception as e:
            return SandboxExecutionResult(
                stdout="".join(stdout_parts),
                stderr="".join(stderr_parts),
                exit_code=-1,
                error=str(e),
            )

        return SandboxExecutionResult(
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            exit_code=exit_code,
            images=images,
        )

    async def close(self):
        """关闭与 Jupyter Kernel 的 WebSocket 通道连接"""
        if self.ws and not self.ws.closed:
            await self.ws.close()
            self.ws = None


class JupyterServerManager:
    """管理沙箱内的 Jupyter 引擎生命周期及长连接"""

    def __init__(self, session: BaseSandboxSession):
        """初始化 Jupyter 服务生命周期及会话连接管理器"""
        self.session = session
        self._http_session: aiohttp.ClientSession | None = None
        self.base_url = ""
        self.ws_url = ""
        self._is_started = False
        self._clients: dict[str, JupyterWSClient] = {}

    async def ensure_started(self, env_vars: dict[str, str] | None = None):
        """确保沙箱环境内部已拉起并运行着 Jupyter Server"""
        if self._is_started:
            return

        jupyter_port = self.session.get_meta("jupyter_port")
        if not jupyter_port:
            raise RuntimeError("沙箱未分配或映射 Jupyter 端口，无法建立服务")

        check_jupyter = await self.session.run_process("command -v jupyter-server")
        if check_jupyter.error:
            raise RuntimeError(
                f"检查 jupyter-server 失败 (系统错误): {check_jupyter.error}"
            )
        if check_jupyter.exit_code != 0:
            raise RuntimeError("沙箱内未安装 jupyter-server，请检查 Blueprint")

        await self.session.run_process(
            "mkdir -p /tmp/jupyter_runtime /tmp/jupyter_data && "
            "chmod 777 /tmp/jupyter_runtime /tmp/jupyter_data"
        )

        await self.session.run_process("pkill -9 -f jupyter-server || true")
        await asyncio.sleep(0.5)

        env_str = " ".join([f"{k}={v}" for k, v in (env_vars or {}).items()])
        start_cmd = (
            f"nohup env {env_str} jupyter-server "
            "--ServerApp.ip=0.0.0.0 --ServerApp.port=8888 "
            "--ServerApp.token='' --ServerApp.password='' "
            "--ServerApp.disable_check_xsrf=True "
            "--ServerApp.allow_origin='*' --ServerApp.allow_root=True "
            f"> {self.session.workspace_path}/jupyter.log 2>&1 &"
        )
        start_res = await self.session.run_process(start_cmd)
        if start_res.error:
            raise RuntimeError(f"执行启动命令失败 (系统错误): {start_res.error}")

        self.base_url = f"http://127.0.0.1:{jupyter_port}"
        self.ws_url = f"ws://127.0.0.1:{jupyter_port}"
        self._http_session = aiohttp.ClientSession()

        for _ in range(15):
            try:
                async with self._http_session.get(
                    f"{self.base_url}/api/kernels"
                ) as resp:
                    if resp.status == 200:
                        self._is_started = True
                        logger.info(
                            "[JupyterManager] Jupyter 引擎拉起成功 "
                            f"(Port: {jupyter_port})"
                        )
                        return
            except Exception:
                pass
            await asyncio.sleep(1)

        log_res = await self.session.run_process(
            f"cat {self.session.workspace_path}/jupyter.log"
        )
        error_details = log_res.stdout if log_res.exit_code == 0 else "无法读取日志"
        raise RuntimeError(
            f"Jupyter 服务动态拉起并等待 API 响应超时。日志内容: {error_details}"
        )

    async def get_client(self, kernel_name: str) -> JupyterWSClient:
        """获取并连接到指定内核的 Jupyter WebSocket 客户端"""
        await self.ensure_started()
        if kernel_name in self._clients:
            return self._clients[kernel_name]

        if not self._http_session:
            raise RuntimeError("HTTP 会话未建立")

        async with self._http_session.post(
            f"{self.base_url}/api/kernels", json={"name": kernel_name}
        ) as resp:
            kernel_id = (await resp.json()).get("id")
            if not kernel_id:
                raise RuntimeError(f"分配 Kernel {kernel_name} 失败")

        client = JupyterWSClient(
            self._http_session, self.base_url, self.ws_url, kernel_id
        )
        self._clients[kernel_name] = client
        return client

    async def close(self):
        """关闭并清理所有 Jupyter WS 连接及 HTTP 会话资源"""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
        if self._http_session:
            await self._http_session.close()
            self._http_session = None
        self._is_started = False


class BaseCodeExecutor(ABC):
    """代码执行器抽象基类"""

    def __init__(self, session: BaseSandboxSession):
        """初始化代码执行器，绑定沙箱会话"""
        self.session = session

    @abstractmethod
    async def execute_code(
        self,
        code: str,
        timeout: int = 30,
        injected_code: str | None = None,
        on_output: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> SandboxExecutionResult:
        """在沙箱会话中执行指定代码并返回执行结果"""
        pass


class GenericCLIExecutor(BaseCodeExecutor):
    """模板驱动的通用代码执行器"""

    def __init__(self, session, profile: "LanguageProfile"):
        """初始化通用命令行代码执行器，传入语言配置模板"""
        super().__init__(session)
        self.profile = profile

    async def execute_code(
        self,
        code: str,
        timeout: int = 30,
        injected_code: str | None = None,
        on_output: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> SandboxExecutionResult:
        """将代码写入临时文件，必要时编译并执行，返回执行结果"""
        env = None
        if injected_code:
            await self.session.write(
                f"{self.session.workspace_path}/zhenxun_host.py",
                injected_code.encode("utf-8"),
            )

        script_path = f"{self.session.workspace_path}/main{self.profile.source_ext}"
        await self.session.write(script_path, code.encode("utf-8"))

        if self.profile.compile_cmd:
            compile_cmd = self.profile.compile_cmd.format(source_file=script_path)
            res = await self.session.run_process(
                compile_cmd, timeout=timeout, env=env, on_output=on_output
            )
            if res.exit_code != 0:
                return res

        run_cmd = self.profile.run_cmd.format(source_file=script_path)
        return await self.session.run_process(
            run_cmd, timeout=timeout, env=env, on_output=on_output
        )


class CodeExecutorRegistry:
    """多语言代码执行器动态注册中心"""

    _executors: ClassVar[
        dict[str, dict[str, dict[bool, Callable[[Any], BaseCodeExecutor]]]]
    ] = {}
    _profiles: ClassVar[dict[str, "LanguageProfile"]] = {}

    _aliases: ClassVar[dict[str, str]] = {
        "py": "python",
        "js": "javascript",
        "sh": "bash",
        "shell": "bash",
        "ts": "typescript",
    }

    @classmethod
    def _normalize_lang(cls, language: str) -> str:
        """规范化语言名称，转换为统一小写格式并应用别名"""
        lang_lower = language.lower().strip()
        return cls._aliases.get(lang_lower, lang_lower)

    @classmethod
    def register(
        cls,
        language: str,
        executor_cls: Callable[[Any], BaseCodeExecutor],
        is_stateful: bool = False,
        scope: str | None = None,
    ) -> None:
        """注册一个语言对应的代码执行器构造工厂"""
        ns = scope if scope is not None else infer_plugin_namespace()
        lang_norm = cls._normalize_lang(language)

        if ns not in cls._executors:
            cls._executors[ns] = {}
        if lang_norm not in cls._executors[ns]:
            cls._executors[ns][lang_norm] = {}

        cls._executors[ns][lang_norm][is_stateful] = executor_cls
        logger.debug(
            f"[CodeExecutorRegistry] 成功注册执行器: {ns} -> {lang_norm} "
            f"(Stateful: {is_stateful})"
        )

    @classmethod
    def register_jupyter_language(
        cls,
        language: str,
        kernel_name: str,
        scope: str | None = None,
    ) -> None:
        """快速注册一个基于 Jupyter 内核的有状态运行语言"""
        cls.register(
            language,
            lambda session: GenericJupyterExecutor(session, kernel_name=kernel_name),
            is_stateful=True,
            scope=scope,
        )

    @classmethod
    def register_profile(cls, profile: "LanguageProfile") -> None:
        """注册一个基于命令行的无状态运行语言配置模板"""
        cls._profiles[profile.language.lower()] = profile
        for alias in profile.aliases:
            cls._profiles[alias.lower()] = profile
        logger.debug(f"[CodeExecutorRegistry] 成功注册语言配置模板: {profile.language}")

    @classmethod
    def create_executor(
        cls, language: str, needs_state: bool, session: Any, namespace: str = "global"
    ) -> BaseCodeExecutor:
        """根据语言和有无状态需求为指定会话创建具体的执行器实例"""
        lang_norm = cls._normalize_lang(language)

        for target_ns in [namespace, "global"]:
            if target_ns in cls._executors and lang_norm in cls._executors[target_ns]:
                lang_executors = cls._executors[target_ns][lang_norm]
                if needs_state and True in lang_executors:
                    return lang_executors[True](session)
                if False in lang_executors:
                    return lang_executors[False](session)

        for ns_dict in cls._executors.values():
            if lang_norm in ns_dict:
                lang_executors = ns_dict[lang_norm]
                if needs_state and True in lang_executors:
                    return lang_executors[True](session)
                if False in lang_executors:
                    return lang_executors[False](session)

        if lang_norm in cls._profiles:
            return GenericCLIExecutor(session, cls._profiles[lang_norm])

        raise ValueError(
            f"当前沙箱生态未提供针对语言 '{language}' 的代码执行器。"
            f"支持的语言有: {cls.get_supported_languages()}"
        )

    @classmethod
    def get_supported_languages(cls, namespace: str = "global") -> list[str]:
        """获取所有目前已注册支持的编程语言列表"""
        langs = set(cls._executors.get("global", {}).keys())
        if namespace in cls._executors:
            langs.update(cls._executors[namespace].keys())
        langs.update(cls._profiles.keys())
        return list(langs)


class GenericJupyterExecutor(BaseCodeExecutor):
    """基于 Jupyter 协议的泛化有状态执行器。支持多语言 REPL。"""

    def __init__(self, session, kernel_name: str = "python3"):
        """初始化泛用 Jupyter 有状态执行器，设置默认内核"""
        super().__init__(session)
        self.kernel_name = kernel_name
        self.manager = JupyterServerManager(session)

    async def execute_code(
        self,
        code: str,
        timeout: int = 30,
        injected_code: str | None = None,
        on_output: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> SandboxExecutionResult:
        """利用 Jupyter 内核长连接异步执行代码并返回运行结果"""
        try:
            await self.manager.ensure_started()
        except Exception as e:
            return SandboxExecutionResult(exit_code=-1, error=str(e))

        if injected_code:
            await self.session.write(
                f"{self.session.workspace_path}/zhenxun_host.py",
                injected_code.encode("utf-8"),
            )

        try:
            client = await self.manager.get_client(self.kernel_name)
        except Exception as e:
            return SandboxExecutionResult(exit_code=-1, error=str(e))

        result = await client.execute(code, timeout=timeout, on_output=on_output)
        return result

    async def close(self):
        """关闭 Jupyter 客户端及后台运行环境"""
        await self.manager.close()


CodeExecutorRegistry.register_profile(
    LanguageProfile(
        language="python",
        aliases=["py"],
        source_ext=".py",
        run_cmd="python3 {source_file}",
    )
)
CodeExecutorRegistry.register_profile(
    LanguageProfile(
        language="bash",
        aliases=["sh", "shell"],
        source_ext=".sh",
        run_cmd="bash {source_file}",
    )
)
CodeExecutorRegistry.register_profile(
    LanguageProfile(
        language="javascript",
        aliases=["js", "node"],
        source_ext=".js",
        run_cmd="node {source_file}",
    )
)
CodeExecutorRegistry.register_jupyter_language("python", "python3", scope="global")

__all__ = [
    "BaseCodeExecutor",
    "CodeExecutorRegistry",
    "GenericCLIExecutor",
    "GenericJupyterExecutor",
    "get_execution_command",
]

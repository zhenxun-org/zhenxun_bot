from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .models import SandboxExecutionResult


class InteractiveTerminalSession(Protocol):
    @abstractmethod
    async def start(self, cmd: str, env: dict[str, str] | None = None) -> None:
        """启动并挂载终端会话"""
        ...

    @abstractmethod
    async def send_input(self, text: str) -> None:
        """向终端发送标准输入"""
        ...

    @abstractmethod
    async def read_output(self, timeout: int = 5) -> str:
        """读取当前终端屏幕输出画面"""
        ...

    @abstractmethod
    async def interrupt(self) -> None:
        """发送强制中断信号(Ctrl+C)"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """释放并关闭终端资源"""
        ...


@runtime_checkable
class SupportsCommandExecution(Protocol):
    async def run_process(
        self,
        command: str | list[str],
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        on_output: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> SandboxExecutionResult:
        """在沙箱内单次执行短命令并获取结果"""
        ...


@runtime_checkable
class SandboxProcessStream(Protocol):
    @abstractmethod
    async def read(self) -> "ProcessStreamMessage | None":
        """异步读取下一块输出流数据"""
        ...

    @abstractmethod
    async def write(self, data: bytes) -> None:
        """异步写入数据到进程标准输入"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭并终止输入输出流"""
        ...


@runtime_checkable
class SupportsStreamExecution(Protocol):
    @abstractmethod
    def create_stream_process(
        self,
        command: str | list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> AbstractAsyncContextManager[SandboxProcessStream]:
        """创建持久流式后台进程，供长连接通信"""
        ...


@runtime_checkable
class SupportsInteractivePTY(Protocol):
    async def create_pty_session(self) -> InteractiveTerminalSession:
        """创建分配一个真实的伪终端(PTY)交互会话"""
        ...


@runtime_checkable
class SupportsFileSystem(Protocol):
    async def write_raw_file(self, path: str | Path, content: str) -> bool:
        """使用字符串极速覆写文件"""
        ...

    async def read_raw_file(self, path: str | Path) -> str:
        """直接读取文件内容为文本字符串"""
        ...

    async def delete_raw_file(self, path: str | Path) -> bool:
        """直接删除指定物理文件"""
        ...

    async def upload_raw_dir(
        self, local_dir_path: str | Path, sandbox_target_path: str | Path
    ) -> bool:
        """将宿主机本地目录完整打包上传至沙箱"""
        ...

    async def write(self, path: str | Path, data: bytes) -> bool:
        """底层二进制安全写入文件"""
        ...

    async def read(self, path: str | Path) -> bytes:
        """底层二进制安全读取文件"""
        ...

    async def rm(self, path: str | Path, recursive: bool = False) -> bool:
        """执行标准的 rm 删除操作"""
        ...

    async def mkdir(self, path: str | Path, parents: bool = False) -> bool:
        """执行标准的 mkdir 创建目录操作"""
        ...


@runtime_checkable
class SupportsPortMapping(Protocol):
    def get_meta(self, key: str, default: Any = None) -> Any:
        """获取沙箱驱动映射的底层元数据(如分配的随机端口)"""
        ...


class SandboxChannel(ABC):
    @abstractmethod
    def get_meta(self, key: str, default: Any = None) -> Any:
        """获取沙箱会话的底层元数据字典"""
        ...


class StatefulCodeClient(Protocol):
    """有状态代码执行客户端通信协议"""

    @abstractmethod
    async def execute(
        self,
        code: str,
        timeout: int = 30,
        on_output: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> SandboxExecutionResult:
        """执行指定代码并获取结果"""
        ...

    @abstractmethod
    async def interrupt(self) -> None:
        """发送强制中断信号(模拟Ctrl+C)"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭底层网络及进程连接"""
        ...


class BaseEngineManager(Protocol):
    """沙箱后台引擎生命周期管理器协议"""

    @abstractmethod
    async def ensure_started(self, env_vars: dict[str, str] | None = None) -> None:
        """确保后台引擎主服务已在沙箱中成功启动"""
        ...

    @abstractmethod
    async def get_client(self, kernel_name: str) -> StatefulCodeClient:
        """分配并获取指定语言内核的通信客户端"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """安全关闭引擎并回收所有分配的客户端资源"""
        ...


@dataclass
class ProcessStreamMessage:
    """统一的进程流消息载体"""

    stream_type: int
    data: bytes

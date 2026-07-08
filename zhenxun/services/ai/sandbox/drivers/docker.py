import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import io
from pathlib import Path
import re
import tarfile
from typing import Any, ClassVar

import aiodocker
import anyio

from zhenxun.configs.config import BotConfig
from zhenxun.services.ai.config import get_llm_config
from zhenxun.services.ai.core.exceptions import SandboxPathEscapeError, WorkspaceIOError
from zhenxun.services.ai.sandbox.models import (
    SandboxBlueprint,
    SandboxExecutionResult,
    SandboxSessionState,
)
from zhenxun.services.ai.sandbox.protocols import (
    InteractiveTerminalSession,
    ProcessStreamMessage,
    SandboxProcessStream,
)
from zhenxun.services.ai.sandbox.storage import RESOLVE_PATH_HELPER, coerce_posix_path
from zhenxun.services.ai.utils.logger import log_sandbox as logger

from .base import BaseSandboxClient, BaseSandboxSession


class DockerInteractiveTerminalSession(InteractiveTerminalSession):
    """PTY 交互式会话：接管 Docker Stream，带有防死循环 Token 截断机制"""

    def __init__(self, session: "DockerSandboxSession"):
        """初始化 Docker PTY 交互式会话实例"""
        self.session = session
        self.exec_stream = None
        self.buffer = ""
        self._read_task = None
        self.ansi_escape = re.compile(r"(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]")

    async def start(self, cmd: str, env: dict[str, str] | None = None) -> None:
        """在容器中异步开启 PTY 终端执行指定命令"""
        if not self.session.container:
            raise RuntimeError("沙箱容器未启动")

        cmd_list = ["/bin/sh", "-c", cmd] if isinstance(cmd, str) else cmd
        env_list = [f"{k}={v}" for k, v in env.items()] if env else None

        await self.session._ensure_workspace()
        exec_inst = await self.session.container.exec(
            cmd=cmd_list,
            tty=True,
            stdin=True,
            stdout=True,
            stderr=True,
            workdir=self.session.workspace_path,
            environment=env_list,
        )

        self.exec_stream = exec_inst.start(detach=False)
        await self.exec_stream.__aenter__()

        self._read_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        """异步循环读取容器执行输出流，并写入本地缓冲区（包含防超长截断）"""
        try:
            while True:
                if not self.exec_stream:
                    break
                msg = await self.exec_stream.read_out()
                if msg is None:
                    break
                text = msg.data.decode("utf-8", errors="replace")
                clean_text = self.ansi_escape.sub("", text)
                self.buffer += clean_text

                if len(self.buffer) > 20000:
                    self.buffer = (
                        "...\n[系统保护：已强行丢弃早期超长输出]\n"
                        + self.buffer[-19000:]
                    )
        except Exception as e:
            logger.debug(f"[PTY] 流读取结束: {e}")

    async def send_input(self, text: str) -> None:
        """向容器的 PTY 终端输入标准输入数据"""
        if self.exec_stream:
            await self.exec_stream.write_in(text.encode("utf-8"))

    async def read_output(self, timeout: int = 5) -> str:  # noqa: ASYNC109
        """获取缓冲区最新的 50 行终端输出内容"""
        lines = self.buffer.split("\n")
        return "\n".join(lines[-50:]).strip()

    async def interrupt(self) -> None:
        """发送 Ctrl+C 中断信号以打断当前执行进程"""
        if self.exec_stream:
            await self.exec_stream.write_in(b"\x03")

    async def close(self) -> None:
        """关闭 PTY 读取循环任务和 exec 流资源"""
        if self._read_task:
            self._read_task.cancel()
        if self.exec_stream:
            await self.exec_stream.close()
            self.exec_stream = None


class DockerSandboxProcessStream(SandboxProcessStream):
    """包装 aiodocker 的流，使其符合 SandboxProcessStream 协议"""

    def __init__(self, docker_stream):
        """初始化封装的 Docker 流程管道流"""
        self.stream = docker_stream

    async def read(self) -> ProcessStreamMessage | None:
        """异步读取管道流中的数据块并转换为 ProcessStreamMessage"""
        msg = await self.stream.read_out()
        if msg is None:
            return None
        return ProcessStreamMessage(stream_type=msg.stream, data=msg.data)

    async def write(self, data: bytes) -> None:
        """向管道中异步写入数据字节"""
        await self.stream.write_in(data)

    async def close(self) -> None:
        """关闭 Docker 管道流资源"""
        await self.stream.close()


class DockerSandboxSession(BaseSandboxSession):
    """Docker 驱动底层的具体沙箱会话通道实现"""

    def __init__(
        self,
        state: SandboxSessionState,
        container: Any,
    ):
        """初始化 Docker 沙箱会话并传入 Docker 容器句柄"""
        super().__init__(state)
        self.container = container
        self._vfs_helper_installed = False
        self._workspace_created = False

    async def is_alive(self) -> bool:
        """查询底层 Docker 容器是否正处于 Running 状态"""
        if not self.container:
            return False
        try:
            info = await self.container.show()
            return info.get("State", {}).get("Running", False)
        except Exception:
            return False

    async def create_pty_session(self) -> InteractiveTerminalSession:
        """创建交互式 Docker 终端会话实例"""
        return DockerInteractiveTerminalSession(self)

    async def _ensure_workspace(self):
        """确保在容器内成功创建该会话的工作空间目录"""
        if not self._workspace_created and self.container:
            exec_inst = await self.container.exec(
                cmd=["/bin/sh", "-c", f"mkdir -p '{self.workspace_path}'"]
            )
            async with exec_inst.start(detach=False) as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        break
            self._workspace_created = True

    @asynccontextmanager
    async def create_stream_process(
        self,
        command: str | list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> AsyncGenerator[SandboxProcessStream, None]:
        """在指定工作目录下创建一个流式交互的进程通道"""
        cmd_list = ["/bin/sh", "-c", command] if isinstance(command, str) else command
        env_list = [f"{k}={v}" for k, v in env.items()] if env else None

        await self._ensure_workspace()
        exec_inst = await self.container.exec(
            cmd=cmd_list,
            stdin=True,
            stdout=True,
            stderr=True,
            environment=env_list,
            workdir=cwd or self.workspace_path,
        )
        async with exec_inst.start(detach=False) as raw_stream:
            yield DockerSandboxProcessStream(raw_stream)

    async def _ensure_vfs_helper(self):
        """确保在沙箱容器内装有路径安全分析二进制文件"""
        if self._vfs_helper_installed:
            return
        check = await self.run_process(f"test -x {RESOLVE_PATH_HELPER.install_path}")
        if check.exit_code != 0:
            res = await self.run_process(RESOLVE_PATH_HELPER.install_command())
            if res.exit_code != 0 or res.error:
                raise RuntimeError(f"安装沙箱 VFS 探针失败: {res.stderr or res.error}")
        self._vfs_helper_installed = True

    async def _validate_remote_path(
        self, path: str | Path, for_write: bool = False, base_dir: str | None = None
    ) -> Path:
        """使用 VFS 探针对给定的沙箱路径进行安全性防逃逸校验"""
        base_dir = base_dir or self.workspace_path
        target_posix = coerce_posix_path(path).as_posix()
        is_write = "1" if for_write else "0"

        if not target_posix.startswith("/"):
            import posixpath

            target_posix = posixpath.normpath(f"{base_dir}/{target_posix}")

        if not get_llm_config().sandbox.enable_vfs_helper:
            return Path(target_posix)

        await self._ensure_vfs_helper()

        cmd = [str(RESOLVE_PATH_HELPER.install_path), base_dir, target_posix, is_write]
        res = await self.run_process(cmd)

        if res.exit_code == 0:
            resolved = res.stdout.strip()
            if not resolved:
                raise WorkspaceIOError(str(path), "路径解析返回为空")
            return Path(resolved)
        if res.exit_code == 111:
            resolved_path = res.stderr.replace("workspace escape: ", "").strip()
            raise SandboxPathEscapeError(path=str(path), resolved_path=resolved_path)
        raise WorkspaceIOError(str(path), f"探针解析路径异常: {res.stderr}")

    async def run_process(
        self,
        command: str | list[str],
        cwd: str | None = None,
        timeout: float | None = 30.0,  # noqa: ASYNC109
        env: dict[str, str] | None = None,
        on_output: Any = None,
    ) -> SandboxExecutionResult:
        """在容器中指定目录下执行非交互式进程并等待其运行结果"""
        from zhenxun.services.ai.core.exceptions import SandboxFatalError

        self.touch()
        if not self.container:
            raise SandboxFatalError("沙箱容器未启动或句柄已丢失。")

        if not await self.is_alive():
            raise SandboxFatalError(
                f"沙箱容器 '{self.state.container_name}' 已意外死亡 "
                "(可能遭遇 WSL OOMKiller 或被宿主机强杀)。"
            )

        cmd_list = ["/bin/sh", "-c", command] if isinstance(command, str) else command
        env_list = [f"{k}={v}" for k, v in env.items()] if env else None

        try:
            await self._ensure_workspace()
            exec_inst = await self.container.exec(
                cmd=cmd_list,
                stdout=True,
                stderr=True,
                workdir=cwd or self.workspace_path,
                environment=env_list,
            )
            stdout_buf = bytearray()
            stderr_buf = bytearray()
            is_timeout = False

            async def _read_stream():
                async with exec_inst.start(detach=False) as stream:
                    while True:
                        msg = await stream.read_out()
                        if msg is None:
                            break
                        if msg.stream == 1:
                            stdout_buf.extend(msg.data)
                            if on_output:
                                await on_output("stdout", msg.data)
                        elif msg.stream == 2:
                            stderr_buf.extend(msg.data)
                            if on_output:
                                await on_output("stderr", msg.data)

            try:
                await asyncio.wait_for(_read_stream(), timeout=timeout)
            except asyncio.TimeoutError:
                is_timeout = True

            info = await exec_inst.inspect()
            exit_code = info.get("ExitCode", -1)

            return SandboxExecutionResult(
                stdout=stdout_buf.decode("utf-8", errors="replace").strip(),
                stderr=stderr_buf.decode("utf-8", errors="replace").strip(),
                exit_code=exit_code,
                is_timeout=is_timeout,
            )
        except Exception as e:
            return SandboxExecutionResult(exit_code=-1, error=str(e))

    async def read(self, path: str | Path) -> bytes:
        """通过 Docker Tar 归档接口读取容器内的指定文件内容"""
        self.touch()
        secure_path = await self._validate_remote_path(path, for_write=False)
        try:
            tar_obj: tarfile.TarFile = await self.container.get_archive(
                secure_path.as_posix()
            )

            members = tar_obj.getmembers()
            if not members:
                return b""

            f = tar_obj.extractfile(members[0])
            return f.read() if f else b""
        except Exception as e:
            raise WorkspaceIOError(str(path), f"读取文件异常: {e}")

    async def write(self, path: str | Path, data: bytes) -> bool:
        """通过 Docker put_archive 接口将文件写入容器的指定路径"""
        self.touch()
        secure_path = await self._validate_remote_path(path, for_write=True)

        def _create_tar():
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                tarinfo = tarfile.TarInfo(name=secure_path.name)
                tarinfo.size = len(data)
                tar.addfile(tarinfo, io.BytesIO(data))
            return buf.getvalue()

        try:
            await self.run_process(f"rm -f '{secure_path.as_posix()}'")

            await self.mkdir(secure_path.parent, parents=True)
            tar_bytes = await asyncio.to_thread(_create_tar)
            await self.container.put_archive(secure_path.parent.as_posix(), tar_bytes)
            return True
        except Exception as e:
            logger.error(f"[Docker I/O] 写入失败: {e}")
            return False

    async def rm(self, path: str | Path, recursive: bool = False) -> bool:
        """在容器内执行 rm 命令移除指定文件或目录"""
        secure_path = await self._validate_remote_path(path, for_write=True)
        flag = "-rf" if recursive else "-f"
        res = await self.run_process(f"rm {flag} '{secure_path.as_posix()}'")
        return res.exit_code == 0

    async def mkdir(self, path: str | Path, parents: bool = False) -> bool:
        """在容器内执行 mkdir 命令创建目录"""
        secure_path = await self._validate_remote_path(path, for_write=True)
        flag = "-p" if parents else ""
        res = await self.run_process(f"mkdir {flag} '{secure_path.as_posix()}'")
        return res.exit_code == 0

    async def upload_raw_dir(
        self, local_dir_path: str, sandbox_target_path: str
    ) -> bool:
        """通过打包 tar 归档将宿主机本地目录上传至容器内指定路径"""
        aio_path = anyio.Path(local_dir_path)
        if not await aio_path.exists() or not await aio_path.is_dir():
            return False

        local_path = Path(local_dir_path)

        def _create_tar():
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                for item in local_path.rglob("*"):
                    if item.is_file():
                        arcname = item.relative_to(local_path).as_posix()
                        tar.add(item, arcname=arcname)
            return buf.getvalue()

        try:
            await self.mkdir(sandbox_target_path, parents=True)
            tar_bytes = await asyncio.to_thread(_create_tar)
            await self.container.put_archive(sandbox_target_path, tar_bytes)
            return True
        except Exception as e:
            logger.error(f"[Docker I/O] 上传目录失败: {e}")
            return False

    async def close(self) -> None:
        """关闭会话并在容器内清理本会话对应的工作空间目录"""
        try:
            if await self.is_alive():
                await self.rm(self.workspace_path, recursive=True)
        except Exception as e:
            logger.error(f"清理沙箱会话工作区 {self.session_id} 异常: {e}")


class DockerSandboxClient(BaseSandboxClient):
    """基于 Docker 实现的沙箱物理资源管理器类"""

    backend_id = "docker"
    _global_docker_client: ClassVar[Any] = None
    _containers: ClassVar[dict[str, Any]] = {}
    _jupyter_ports: ClassVar[dict[str, int]] = {}
    _init_lock = asyncio.Lock()
    _engine_available = False

    async def create(
        self,
        session_id: str,
        blueprint: SandboxBlueprint | None = None,
    ) -> BaseSandboxSession:
        """建立或复用物理容器，并为该会话初始化专属的工作目录和 Python 虚拟环境"""
        bp = blueprint or SandboxBlueprint()
        eff_image = bp.image or get_llm_config().sandbox.docker_image
        eff_cname = bp.container_name

        proxy_envs = []
        if BotConfig.system_proxy:
            sandbox_proxy = BotConfig.system_proxy.replace(
                "127.0.0.1", "host.docker.internal"
            ).replace("localhost", "host.docker.internal")

            proxy_envs = [
                f"HTTP_PROXY={sandbox_proxy}",
                f"HTTPS_PROXY={sandbox_proxy}",
                f"http_proxy={sandbox_proxy}",
                f"https_proxy={sandbox_proxy}",
                f"ALL_PROXY={sandbox_proxy}",
            ]

        async with self._init_lock:
            if DockerSandboxClient._global_docker_client is None:
                import aiodocker

                temp_client = aiodocker.Docker()
                try:
                    await asyncio.wait_for(temp_client.system.info(), timeout=5.0)
                    DockerSandboxClient._global_docker_client = temp_client
                    DockerSandboxClient._engine_available = True
                except Exception as e:
                    await temp_client.close()
                    from zhenxun.services.ai.core.exceptions import SandboxFatalError

                    raise SandboxFatalError(
                        f"无法连接到本地 Docker 引擎 (引擎未启动或无权限): {e}"
                    )

            if eff_cname in DockerSandboxClient._containers:
                try:
                    c = DockerSandboxClient._containers[eff_cname]
                    info = await c.show()
                    if not info.get("State", {}).get("Running", False):
                        raise RuntimeError("Container is not running")
                except Exception:
                    DockerSandboxClient._containers.pop(eff_cname, None)
                    DockerSandboxClient._jupyter_ports.pop(eff_cname, None)

            if eff_cname not in DockerSandboxClient._containers:
                port_bindings = {}
                import socket

                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", 0))
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    jupyter_port = s.getsockname()[1]
                port_bindings = {"8888/tcp": [{"HostPort": str(jupyter_port)}]}

                from zhenxun.configs.path_config import DATA_PATH

                safe_image_name = eff_image.replace(":", ".").replace("/", "_")

                global_env_dir = (
                    DATA_PATH / "ai" / "sandbox" / safe_image_name / eff_cname / "env"
                )
                global_env_dir.mkdir(parents=True, exist_ok=True)

                global_home_dir = (
                    DATA_PATH / "ai" / "sandbox" / safe_image_name / eff_cname / "home"
                )
                global_home_dir.mkdir(parents=True, exist_ok=True)

                binds = [f"{global_env_dir.resolve().as_posix()}:/global_env:rw"]
                binds.append(f"{global_home_dir.resolve().as_posix()}:/root:rw")
                if bp.bind_mounts:
                    for mount in bp.bind_mounts:
                        mode = "ro" if mount.read_only else "rw"
                        binds.append(f"{mount.host_path}:{mount.sandbox_path}:{mode}")

                container_config = {
                    "Image": eff_image,
                    "Env": [
                        "npm_config_prefix=/global_env/npm",
                        "VIRTUAL_ENV=/global_env/python_venv",
                        "PATH=/global_env/python_venv/bin:/global_env/npm/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                        "NODE_PATH=/global_env/npm/lib/node_modules",
                        "npm_config_cache=/tmp/npm_cache",
                        "PIP_CACHE_DIR=/tmp/pip_cache",
                        "YARN_CACHE_FOLDER=/tmp/yarn_cache",
                        "HF_HOME=/tmp/hf_cache",
                        "JUPYTER_RUNTIME_DIR=/tmp/jupyter_runtime",
                        "JUPYTER_DATA_DIR=/tmp/jupyter_data",
                        *proxy_envs,
                    ],
                    "Cmd": [
                        "/bin/sh",
                        "-c",
                        "mkdir -p /workspace /tmp/jupyter_runtime /tmp/jupyter_data "
                        "&& chmod 700 /tmp/jupyter_runtime "
                        "&& tail -f /dev/null",
                    ],
                    "HostConfig": {
                        "PortBindings": port_bindings,
                        "Binds": binds,
                        "ExtraHosts": ["host.docker.internal:host-gateway"],
                    },
                    "Labels": {
                        "zhenxun_component": "sandbox",
                        "zhenxun_container_name": eff_cname,
                    },
                }

                import uuid

                name = f"zx_sandbox_{eff_cname}_{uuid.uuid4().hex[:8]}"

                try:
                    container = (
                        await DockerSandboxClient._global_docker_client.containers.run(
                            config=container_config, name=name
                        )
                    )
                except Exception as ex:
                    from zhenxun.services.ai.core.exceptions import SandboxFatalError

                    err_msg = str(ex)
                    if isinstance(ex, AssertionError):
                        err_msg = (
                            "aiodocker AssertionError (可能因宿主机/WSL不支持"
                            "某些 Docker 挂载特性或端口冲突导致被内核驳回)"
                        )
                    raise SandboxFatalError(
                        f"Docker API 拒绝了容器创建请求。底层原因: {err_msg}"
                    )

                DockerSandboxClient._containers[eff_cname] = container
                DockerSandboxClient._jupyter_ports[eff_cname] = jupyter_port
                logger.info(f"已启动物理隔离容器: {eff_cname} (镜像: {eff_image})")

        state = SandboxSessionState(
            session_id=session_id,
            backend_id=self.backend_id,
            container_name=eff_cname,
            sandbox_type=self.backend_id,
        )
        session = DockerSandboxSession(
            state, DockerSandboxClient._containers[eff_cname]
        )
        if eff_cname in DockerSandboxClient._jupyter_ports:
            session._meta["jupyter_port"] = DockerSandboxClient._jupyter_ports[
                eff_cname
            ]

        check_venv = await session.run_process(
            "test -x /global_env/python_venv/bin/pip"
        )
        if check_venv.exit_code != 0:
            logger.info(f"正在初始化/修复容器 [{eff_cname}] 的共享 Python 虚拟环境...")
            init_res = await session.run_process(
                "rm -rf /global_env/python_venv && "
                "uv venv --seed --system-site-packages /global_env/python_venv || "
                "python3 -m venv --system-site-packages /global_env/python_venv"
            )
            if init_res.exit_code != 0:
                logger.error(
                    f"初始化虚拟环境失败: {init_res.stderr or init_res.stdout}"
                )

        return session

    async def resume(self, state: SandboxSessionState) -> BaseSandboxSession:
        """暂不支持通过还原状态重建 Docker 沙箱会话"""
        raise NotImplementedError("Docker Driver 不支持无状态重建恢复。")

    async def delete(self, session: BaseSandboxSession) -> None:
        """清理会话工作区，并当物理容器处于长闲置时触发物理销毁回收"""
        await session.close()

        from zhenxun.services.ai.sandbox.manager import sandbox_manager

        cname = session.state.container_name

        in_use = any(
            s.state.container_name == cname
            for sid, s in sandbox_manager._active_sessions.items()
            if sid != session.session_id
        )

        if not in_use and cname in self._containers:
            try:
                await self._containers[cname].delete(force=True)
                self._containers.pop(cname, None)
                self._jupyter_ports.pop(cname, None)
                logger.info(f"物理容器 {cname} 已长时间闲置，已触发彻底销毁释放内存。")
            except Exception as e:
                self._containers.pop(cname, None)
                self._jupyter_ports.pop(cname, None)
                if getattr(e, "status", None) == 404 or "No such container" in str(e):
                    logger.debug(f"物理容器 {cname} 已不存在。")
                else:
                    logger.error(f"闲置销毁物理容器 {cname} 失败: {e}")

    @classmethod
    async def close_env(cls):
        """清理释放全部管理的 Docker 容器并关闭 Docker 客户端连接"""
        for cname, container in cls._containers.items():
            try:
                await container.delete(force=True)
                logger.info(f"已清理物理容器: {cname}")
            except Exception as e:
                if getattr(e, "status", None) == 404 or "No such container" in str(e):
                    logger.debug(f"物理容器 {cname} 已不存在，无需清理。")
                else:
                    logger.error(f"清理物理容器 {cname} 失败: {e}")
        cls._containers.clear()
        cls._jupyter_ports.clear()

        if cls._global_docker_client:
            await cls._global_docker_client.close()
            cls._global_docker_client = None

    @classmethod
    async def silent_prune_orphans(cls):
        """在系统启动时静默搜寻并强力删除带有残留标记的孤儿容器"""
        try:
            async with aiodocker.Docker() as docker:
                await asyncio.wait_for(docker.system.info(), timeout=2.0)
                containers = await docker.containers.list(
                    filters={"label": ["zhenxun_component=sandbox"]}, all=True
                )
                count = 0
                for c in containers:
                    try:
                        await c.delete(force=True)
                        count += 1
                    except Exception:
                        pass
                if count > 0:
                    logger.info(
                        f"静默清理：已成功回收 {count} 个上次异常退出遗留的沙箱容器。",
                        command="SandboxManager",
                    )
        except Exception:
            pass


from zhenxun.services.ai.sandbox.registry import SandboxRegistry

SandboxRegistry.register_client("docker", DockerSandboxClient)

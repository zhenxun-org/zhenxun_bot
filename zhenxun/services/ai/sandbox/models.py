"""
沙箱相关核心类型定义
"""

from abc import ABC, abstractmethod
import asyncio
import hashlib
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from zhenxun.services.ai.utils.logger import log_sandbox as logger
from zhenxun.utils.pydantic_compat import model_dump

if TYPE_CHECKING:
    from .drivers.base import BaseSandboxSession


class LanguageProfile(BaseModel):
    """语言执行配置模板"""

    language: str
    """语言名称"""
    aliases: list[str] = Field(default_factory=list)
    """语言别名列表"""
    source_ext: str
    """源文件后缀名"""
    compile_cmd: str | None = None
    """编译命令"""
    run_cmd: str
    """运行命令"""
    deps_install_cmd: str | None = None
    """依赖安装命令"""


class BaseEntry(BaseModel, ABC):
    """沙箱物化项(Artifacts)抽象基类"""

    @abstractmethod
    async def apply(self, session: "BaseSandboxSession", dest: str) -> None:
        """将当前项物化(应用)到沙箱中的指定目标路径"""
        pass


class MemoryFile(BaseEntry):
    """基于内存字符串或二进制的数据物化"""

    type: Literal["memory_file"] = "memory_file"
    content: str | bytes

    async def apply(self, session: "BaseSandboxSession", dest: str) -> None:
        data = (
            self.content.encode("utf-8")
            if isinstance(self.content, str)
            else self.content
        )
        await session.write(dest, data)


class LocalFile(BaseEntry):
    """基于宿主机本地单文件的物化"""

    type: Literal["local_file"] = "local_file"
    src: str

    async def apply(self, session: "BaseSandboxSession", dest: str) -> None:
        local_path = Path(self.src)
        if await asyncio.to_thread(local_path.is_file):
            content = await asyncio.to_thread(local_path.read_bytes)
            await session.write(dest, content)
        else:
            logger.warning(f"[Sandbox] 本地文件 {self.src} 不存在，已跳过物化。")


class LocalDir(BaseEntry):
    """基于宿主机本地目录的物化 (会自动打包上传)"""

    type: Literal["local_dir"] = "local_dir"
    src: str

    async def apply(self, session: "BaseSandboxSession", dest: str) -> None:
        success = await session.upload_raw_dir(self.src, dest)
        if not success:
            logger.warning(f"[Sandbox] 本地目录 {self.src} 上传失败，已跳过物化。")


EntryUnion = Annotated[MemoryFile | LocalFile | LocalDir, Field(discriminator="type")]


class BaseSetupStep(BaseModel, ABC):
    """环境装配步骤基类，多态支持任何第三方语言的安装"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @abstractmethod
    async def apply(self, session: Any) -> None:
        """在沙箱会话中执行该装配步骤"""
        pass


class AptSetup(BaseSetupStep):
    type: Literal["apt"] = "apt"
    packages: list[str]

    async def apply(self, session: Any) -> None:
        if not self.packages:
            return

        res = await session.run_process("dpkg -l", timeout=10)
        installed_pkgs = set()
        if res.exit_code == 0:
            for line in res.stdout.splitlines():
                if line.startswith("ii "):
                    parts = line.split()
                    if len(parts) >= 2:
                        pkg_name = parts[1].split(":")[0].strip().lower()
                        installed_pkgs.add(pkg_name)

        missing_packages = [
            pkg for pkg in self.packages if pkg.strip().lower() not in installed_pkgs
        ]
        if not missing_packages:
            logger.debug(f"[Sandbox] Apt 依赖已全部满足，跳过安装: {self.packages}")
            return

        pkg_str = " ".join(missing_packages)
        logger.info(f"[Sandbox] 正在安装缺失的系统级依赖 (Apt): {pkg_str}")
        await session.run_process(
            "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
            f"sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {pkg_str}",
            timeout=300,
        )


class PythonSetup(BaseSetupStep):
    type: Literal["python"] = "python"
    packages: list[str]

    async def apply(self, session: Any) -> None:
        if not self.packages:
            return

        res = await session.run_process("python3 -m pip freeze", timeout=10)
        installed_pkgs = set()
        if res.exit_code == 0:
            for line in res.stdout.splitlines():
                if "==" not in line and "@" not in line:
                    continue
                base_name = re.split(r"[=><~!@\[]", line)[0].strip().lower()
                installed_pkgs.add(base_name)

        missing_packages = [
            pkg
            for pkg in self.packages
            if re.split(r"[=><~!@\[]", pkg)[0].strip().lower() not in installed_pkgs
        ]
        if not missing_packages:
            logger.debug(f"[Sandbox] Python 依赖已全部满足，跳过安装: {self.packages}")
            return

        pkg_str = " ".join(missing_packages)
        check_uv = await session.run_process("command -v uv")
        if check_uv.exit_code != 0:
            logger.info("[Sandbox] 未检测到 uv，正在极速下载 uv...")
            await session.run_process(
                "pip install uv --disable-pip-version-check -q", timeout=60
            )
        logger.info(f"[Sandbox] 正在安装缺失的 Python 依赖: {pkg_str}")
        res = await session.run_process(f"uv pip install {pkg_str}", timeout=120)
        if res.exit_code != 0:
            logger.warning(f"[Sandbox] uv 安装报错，尝试降级使用 pip: {res.stderr}")
            await session.run_process(f"pip install {pkg_str} -q", timeout=180)


class NodeSetup(BaseSetupStep):
    type: Literal["node"] = "node"
    packages: list[str]

    async def apply(self, session: Any) -> None:
        if not self.packages:
            return

        res = await session.run_process("npm list -g --depth=0", timeout=15)
        installed_pkgs = set()
        if res.exit_code == 0:
            for line in res.stdout.splitlines():
                match = re.search(r"[├└]──\s+((?:@[^/]+/)?[^@\s]+)", line)
                if match:
                    installed_pkgs.add(match.group(1).strip().lower())

        missing_packages = []
        for pkg in self.packages:
            base_name = (
                ("@" + pkg[1:].split("@")[0])
                if pkg.startswith("@")
                else pkg.split("@")[0]
            )
            if base_name.strip().lower() not in installed_pkgs:
                missing_packages.append(pkg)

        if not missing_packages:
            logger.debug(f"[Sandbox] Node 依赖已全部满足，跳过安装: {self.packages}")
            return

        pkg_str = " ".join(missing_packages)
        logger.info(f"[Sandbox] 正在安装缺失的 Node 依赖: {pkg_str}")
        res = await session.run_process(
            f"npm install -g {pkg_str} --no-fund --no-audit --cache /tmp/npm-cache",
            timeout=180,
        )
        if res.exit_code != 0:
            from zhenxun.services.ai.core.exceptions import SandboxFatalError

            raise SandboxFatalError(
                f"Node 依赖安装失败 (Exit Code: {res.exit_code}): "
                f"{res.stderr or res.error}"
            )


class ShellSetup(BaseSetupStep):
    type: Literal["shell"] = "shell"
    scripts: list[str]

    async def apply(self, session: Any) -> None:
        for script in self.scripts:
            logger.info(f"[Sandbox] 正在执行自定义装配脚本: {script}")
            res = await session.run_process(script, timeout=300)
            if res.exit_code != 0:
                from zhenxun.services.ai.core.exceptions import SandboxFatalError

                raise SandboxFatalError(
                    f"自定义装配脚本执行失败: {res.stderr or res.error}"
                )


class BindMount(BaseModel):
    """宿主机物理目录绑定映射配置"""

    host_path: str
    """宿主机绝对路径"""
    sandbox_path: str = "/workspace"
    """沙箱内目标路径，默认覆盖 /workspace"""
    read_only: bool = False
    """是否以只读模式挂载"""


class SandboxBlueprint(BaseModel):
    """沙箱大一统声明式蓝图配置"""

    sandbox_type: str = Field(default="auto")
    """强制驱动类型，如 docker 或 local"""
    enable_network: bool = Field(default=False)
    """是否允许访问外网"""
    needs_state: bool = Field(default=False)
    """是否需要持久化状态"""
    image: str | None = Field(default=None)
    """自定义镜像名称。为空则使用全局默认配置"""
    container_name: str = Field(default="global")
    """容器/命名空间名称。相同名称将共享同一个物理容器和依赖环境"""

    setup_steps: list[BaseSetupStep] = Field(default_factory=list)
    """多态环境装配图元管线"""

    entries: dict[str, EntryUnion] = Field(default_factory=dict)
    """沙箱物化节点树(Artifacts)，键为沙箱内的相对目标路径"""
    env: dict[str, str] = Field(default_factory=dict)
    """环境变量"""

    required_extensions: list[str] = Field(default_factory=list)
    """需要自动挂载的扩展"""

    bind_mounts: list[BindMount] = Field(default_factory=list)
    """宿主机目录物理挂载映射列表 (Bind Mounts)"""

    def with_sandbox_type(self, sandbox_type: str) -> "SandboxBlueprint":
        """设置沙箱驱动类型"""
        self.sandbox_type = sandbox_type
        return self

    def with_network(self, enable: bool = True) -> "SandboxBlueprint":
        """设置是否启用网络访问"""
        self.enable_network = enable
        return self

    def with_state(self, enable: bool = True) -> "SandboxBlueprint":
        """设置是否启用状态持久化"""
        self.needs_state = enable
        return self

    def with_image(self, image: str) -> "SandboxBlueprint":
        """声明使用的自定义 Docker 镜像"""
        self.image = image
        return self

    def with_container_name(self, name: str) -> "SandboxBlueprint":
        """声明沙箱使用的物理容器命名空间（实现环境隔离）"""
        self.container_name = name
        return self

    def with_setup_step(self, step: BaseSetupStep) -> "SandboxBlueprint":
        """声明一个多态环境装配步骤"""
        self.setup_steps.append(step)
        return self

    def with_python_packages(self, packages: list[str]) -> "SandboxBlueprint":
        """追加预置 Python 依赖包"""
        self.setup_steps.append(PythonSetup(packages=packages))
        return self

    def with_system_packages(self, packages: list[str]) -> "SandboxBlueprint":
        """追加预置系统包依赖"""
        self.setup_steps.append(AptSetup(packages=packages))
        return self

    def with_node_packages(self, packages: list[str]) -> "SandboxBlueprint":
        """追加预置 Node 依赖包"""
        self.setup_steps.append(NodeSetup(packages=packages))
        return self

    def with_install_scripts(self, scripts: list[str]) -> "SandboxBlueprint":
        """追加自定义安装脚本"""
        self.setup_steps.append(ShellSetup(scripts=scripts))
        return self

    def with_file(self, path: str, content: str | bytes) -> "SandboxBlueprint":
        """声明预置内存字符串或二进制文件"""
        self.entries[path] = MemoryFile(content=content)
        return self

    def with_local_file(self, path: str, local_path: str) -> "SandboxBlueprint":
        """声明预置本地宿主机单文件"""
        self.entries[path] = LocalFile(src=local_path)
        return self

    def with_local_dir(self, path: str, local_dir: str) -> "SandboxBlueprint":
        """声明预置本地宿主机完整目录"""

        for bm in self.bind_mounts:
            if path.startswith(bm.sandbox_path) or bm.sandbox_path.startswith(path):
                logger.warning(
                    f"⚠️ [Sandbox] 路径 '{path}' 和物理挂载映射 "
                    f"'{bm.sandbox_path}' 存在重叠！\n"
                    "继续使用 with_local_dir 打包上传可能会引发冗余上传"
                    "或双向覆盖冲突。建议直接使用物理挂载。"
                )
        self.entries[path] = LocalDir(src=local_dir)
        return self

    def with_env(self, key: str, value: str) -> "SandboxBlueprint":
        """设置环境变量"""
        self.env[key] = value
        return self

    def with_extension(self, extension: str) -> "SandboxBlueprint":
        """声明需要挂载的扩展"""
        if extension not in self.required_extensions:
            self.required_extensions.append(extension)
        return self

    def with_workspace(
        self,
        local_path: str | Path,
        remote_path: str = "/workspace",
        read_only: bool = False,
    ) -> "SandboxBlueprint":
        """声明宿主机物理目录双向挂载 (Bind Mount)"""
        from pathlib import Path

        if remote_path in self.entries:
            logger.warning(
                f"⚠️ [Sandbox] 目标沙箱路径 '{remote_path}' "
                "已包含普通实体映射(如 LocalDir/File)。\n"
                "强制绑定物理挂载 (Bind Mount) 将会遮蔽原有实体。"
            )

        abs_path = Path(local_path).resolve().as_posix()
        self.bind_mounts.append(
            BindMount(host_path=abs_path, sandbox_path=remote_path, read_only=read_only)
        )
        return self

    def calculate_hash(self) -> str:
        """计算当前环境配置的 MD5 指纹，用于缓存命中"""
        entries_dict = {
            k: model_dump(self.entries[k]) for k in sorted(self.entries.keys())
        }

        data_to_hash = {
            "image": self.image,
            "container_name": self.container_name,
            "steps": [model_dump(s) for s in self.setup_steps],
            "entries": entries_dict,
            "bind_mounts": [model_dump(m) for m in self.bind_mounts],
        }
        json_str = json.dumps(data_to_hash, separators=(",", ":"))
        return hashlib.md5(json_str.encode("utf-8")).hexdigest()

    def merge(self, other: "SandboxBlueprint") -> "SandboxBlueprint":
        """合并另一个蓝图配置"""
        if not other:
            return self
        self.enable_network = self.enable_network or other.enable_network
        self.needs_state = self.needs_state or other.needs_state
        self.sandbox_type = (
            other.sandbox_type if other.sandbox_type != "auto" else self.sandbox_type
        )
        self.image = other.image or self.image
        self.container_name = (
            other.container_name
            if other.container_name != "global"
            else self.container_name
        )

        self.setup_steps.extend(other.setup_steps)
        self.entries.update(other.entries)
        self.env.update(other.env)
        self.required_extensions = list(
            dict.fromkeys(self.required_extensions + other.required_extensions)
        )
        self.bind_mounts.extend(other.bind_mounts)
        return self


class SandboxExecutionResult(BaseModel):
    """沙箱统一的执行结果数据结构"""

    stdout: str = Field(default="")
    """标准输出流"""
    stderr: str = Field(default="")
    """标准错误流"""
    exit_code: int = Field(default=0)
    """进程退出码"""
    error: str | None = Field(default=None)
    """框架执行错误信息"""
    is_timeout: bool = Field(default=False)
    """进程是否因超时被挂起(仍在运行)"""
    images: list[str] = Field(default_factory=list)
    """Base64 图片列表"""
    artifacts: dict[str, bytes] = Field(default_factory=dict)
    """生成的文件工件"""

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0 and self.error is None


class SandboxSessionState(BaseModel):
    """沙箱会话的序列化状态，用于无状态恢复"""

    session_id: str
    """会话唯一标识符"""
    backend_id: str
    """后端驱动分配的容器/沙箱 ID"""
    container_name: str = "global"
    """底层所关联的物理容器名称"""
    sandbox_type: str = "docker"
    """沙箱驱动类型"""
    workspace_root_ready: bool = False
    """工作区目录是否已初始化完成"""
    extra_data: dict[str, Any] = Field(default_factory=dict)
    """会话的其它序列化元数据"""

import asyncio
import contextlib
from pathlib import Path
import subprocess
from subprocess import CalledProcessError
from typing import ClassVar

from zhenxun.configs.config import Config
from zhenxun.services.log import logger

LOG_COMMAND = "VirtualEnvPackageManager"
PROJECT_ROOT = Path(__file__).resolve().parents[3]

Config.add_plugin_config(
    "virtualenv",
    "python_path",
    None,
    help="虚拟环境python路径，为空时使用系统环境的uv",
)


class VirtualEnvPackageManager:
    """虚拟环境依赖管理器。

    优先使用 uv 进行依赖管理，若配置了 python_path 或 uv 不可用则回退到 pip，
    并在各操作中根据基础命令自动拼接对应的子命令和参数以兼容两种工具。
    """

    # uv 模式：单个包相关操作统一使用 `uv add --no-sync` 作为基础命令
    UV_BASE: ClassVar[list[str]] = ["uv"]
    UV_ADD_ARGS: ClassVar[list[str]] = ["add", "--no-sync"]
    # pip 模式：基础命令为 `python -m pip` 或环境中的 `pip`
    PIP_BASE_DEFAULT: ClassVar[list[str]] = ["pip"]

    @classmethod
    def __get_base_command(cls) -> tuple[list[str], str]:
        """获取基础命令及当前模式。

        返回:
            (cmd, mode)，其中:
                - cmd 为基础命令前缀（不包含具体子命令和包名）；
                - mode 为 "uv" 或 "pip"。
        """
        # 若配置了虚拟环境 python 路径，则强制使用 pip
        if path := Config.get_config("virtualenv", "python_path"):
            return [path, "-m", "pip"], "pip"

        # 未配置时，优先探测 uv 是否可用
        try:
            subprocess.run(
                ["uv", "--version"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return cls.UV_BASE.copy(), "uv"
        except (CalledProcessError, FileNotFoundError):
            # uv 不可用时回退到系统 pip
            logger.debug("uv 不可用，回退到 pip", LOG_COMMAND)
            return cls.PIP_BASE_DEFAULT.copy(), "pip"

    @classmethod
    def __build_install_command(cls, packages: list[str]) -> list[str]:
        base, mode = cls.__get_base_command()
        if mode == "uv":
            # uv: uv add --no-sync pkg1 pkg2 ...
            return base + cls.UV_ADD_ARGS + packages
        # pip: pip install pkg1 pkg2 ...
        return [*base, "install", *packages]

    @classmethod
    def __build_uninstall_command(cls, packages: list[str]) -> list[str]:
        base, mode = cls.__get_base_command()
        if mode == "uv":
            # uv: uninstall 子命令（目前 uv 支持 uv remove）
            return [*base, "remove", *packages]
        # pip: pip uninstall -y pkg1 pkg2 ...
        return [*base, "uninstall", "-y", *packages]

    @classmethod
    def __build_update_command(cls, packages: list[str]) -> list[str]:
        base, mode = cls.__get_base_command()
        if mode == "uv":
            # uv: 重新 add 即等价于更新（uv 会解析最新版本）
            return base + cls.UV_ADD_ARGS + packages
        # pip: pip install --upgrade pkg1 pkg2 ...
        return [*base, "install", "--upgrade", *packages]

    @classmethod
    def __build_requirements_command(cls, requirement_file: Path) -> list[str]:
        base, mode = cls.__get_base_command()
        req_str = str(requirement_file.absolute())
        if mode == "uv":
            # uv: uv add --requirements requirements.txt --no-sync
            return [*base, *cls.UV_ADD_ARGS, "--requirements", req_str]
        # pip: pip install -r requirements.txt
        return [*base, "install", "-r", req_str]

    @classmethod
    def __build_list_command(cls) -> list[str]:
        base, mode = cls.__get_base_command()
        return [*base, "pip", "list"] if mode == "uv" else [*base, "list"]

    @classmethod
    async def install(cls, package: list[str] | str):
        """安装依赖包。

        参数:
            package: 安装依赖包名称或列表
        """
        if isinstance(package, str):
            package = [package]
        try:
            command = cls.__build_install_command(package)
            logger.info(f"执行虚拟环境安装包指令: {command}", LOG_COMMAND)
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.debug(
                f"安装虚拟环境包指令执行完成: {result.stdout}",
                LOG_COMMAND,
            )
            return result.stdout
        except (CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if isinstance(e, CalledProcessError) else str(e)
            logger.error(f"安装虚拟环境包指令执行失败: {stderr}.", LOG_COMMAND)
            return stderr

    @classmethod
    async def uninstall(cls, package: list[str] | str):
        """卸载依赖包

        参数:
            package: 卸载依赖包名称或列表
        """
        if isinstance(package, str):
            package = [package]
        try:
            command = cls.__build_uninstall_command(package)
            logger.info(f"执行虚拟环境卸载包指令: {command}", LOG_COMMAND)
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.debug(
                f"卸载虚拟环境包指令执行完成: {result.stdout}",
                LOG_COMMAND,
            )
            return result.stdout
        except (CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if isinstance(e, CalledProcessError) else str(e)
            logger.error(f"卸载虚拟环境包指令执行失败: {stderr}.", LOG_COMMAND)
            return stderr

    @classmethod
    async def update(cls, package: list[str] | str):
        """更新依赖包

        参数:
            package: 更新依赖包名称或列表
        """
        if isinstance(package, str):
            package = [package]
        try:
            command = cls.__build_update_command(package)
            logger.info(f"执行虚拟环境更新包指令: {command}", LOG_COMMAND)
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.debug(f"更新虚拟环境包指令执行完成: {result.stdout}", LOG_COMMAND)
            return result.stdout
        except (CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if isinstance(e, CalledProcessError) else str(e)
            logger.error(f"更新虚拟环境包指令执行失败: {stderr}.", LOG_COMMAND)
            return stderr

    @staticmethod
    def _clean_requirements_file(file_path: Path) -> None:
        """清理 requirements 文件中的非ASCII注释

        防止 Windows 上 pip 使用 GBK 编码读取 UTF-8 文件时出错

        参数:
            file_path: requirements 文件路径
        """
        with contextlib.suppress(Exception):
            content = file_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            cleaned_lines = []
            for line in lines:
                stripped = line.strip()
                # 跳过空行
                if not stripped:
                    continue
                # 如果是注释行且包含非ASCII字符，跳过
                if stripped.startswith("#"):
                    try:
                        stripped.encode("ascii")
                    except UnicodeEncodeError:
                        continue
                cleaned_lines.append(line)
            # 写回文件
            file_path.write_text(
                "\n".join(cleaned_lines) + "\n" if cleaned_lines else "",
                encoding="utf-8",
            )

    @classmethod
    async def install_requirement(cls, requirement_file: Path):
        """安装依赖文件

        参数:
            requirement_file: requirement文件路径

        异常:
            FileNotFoundError: 文件不存在
        """
        if not requirement_file.exists():
            raise FileNotFoundError(f"依赖文件 {requirement_file} 不存在", LOG_COMMAND)
        # 清理 requirements 文件中的非ASCII注释，防止 Windows GBK 编码问题
        cls._clean_requirements_file(requirement_file)
        try:
            command = cls.__build_requirements_command(requirement_file)
            logger.info(f"执行虚拟环境安装依赖文件指令: {command}", LOG_COMMAND)
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.debug(
                f"安装虚拟环境依赖文件指令执行完成: {result.stdout}",
                LOG_COMMAND,
            )
            return result.stdout
        except (CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if isinstance(e, CalledProcessError) else str(e)
            logger.error(
                f"安装虚拟环境依赖文件指令执行失败: {stderr}.",
                LOG_COMMAND,
            )
            return stderr

    @classmethod
    async def list(cls) -> str:
        """列出已安装的依赖包"""
        try:
            command = cls.__build_list_command()
            logger.info(f"执行虚拟环境列出包指令: {command}", LOG_COMMAND)
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.debug(
                f"列出虚拟环境包指令执行完成: {result.stdout}",
                LOG_COMMAND,
            )
            return result.stdout
        except (CalledProcessError, FileNotFoundError) as e:
            stderr = e.stderr if isinstance(e, CalledProcessError) else str(e)
            logger.error(f"列出虚拟环境包指令执行失败: {stderr}.", LOG_COMMAND)
        return ""

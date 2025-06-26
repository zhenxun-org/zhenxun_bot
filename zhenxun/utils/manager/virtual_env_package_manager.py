from pathlib import Path
import subprocess
from subprocess import CalledProcessError
from typing import ClassVar

from zhenxun.configs.config import Config
from zhenxun.services.log import logger

BAT_FILE = Path() / "win启动.bat"

LOG_COMMAND = "VirtualEnvPackageManager"

Config.add_plugin_config(
    "virtualenv",
    "python_path",
    None,
    help="虚拟环境python路径，为空时使用系统环境的poetry",
)


class VirtualEnvPackageManager:
    WIN_COMMAND: ClassVar[list[str]] = [
        "./Python310/python.exe",
        "-m",
        "pip",
    ]

    DEFAULT_COMMAND: ClassVar[list[str]] = ["poetry", "run", "pip"]

    @classmethod
    def __get_command(cls) -> list[str]:
        if path := Config.get_config("virtualenv", "python_path"):
            return [path, "-m", "pip"]
        return (
            cls.WIN_COMMAND.copy() if BAT_FILE.exists() else cls.DEFAULT_COMMAND.copy()
        )

    @classmethod
    def install(cls, package: list[str] | str):
        """安装依赖包

        参数:
            package: 安装依赖包名称或列表
        """
        if isinstance(package, str):
            package = [package]
        try:
            command = cls.__get_command()
            command.append("install")
            command.append(" ".join(package))
            logger.info(f"执行虚拟环境安装包指令: {command}", LOG_COMMAND)
            result = subprocess.run(
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
        except CalledProcessError as e:
            logger.error(f"安装虚拟环境包指令执行失败: {e.stderr}.", LOG_COMMAND)
            return e.stderr

    @classmethod
    def uninstall(cls, package: list[str] | str):
        """卸载依赖包

        参数:
            package: 卸载依赖包名称或列表
        """
        if isinstance(package, str):
            package = [package]
        try:
            command = cls.__get_command()
            command.append("uninstall")
            command.append("-y")
            command.append(" ".join(package))
            logger.info(f"执行虚拟环境卸载包指令: {command}", LOG_COMMAND)
            result = subprocess.run(
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
        except CalledProcessError as e:
            logger.error(f"卸载虚拟环境包指令执行失败: {e.stderr}.", LOG_COMMAND)
            return e.stderr

    @classmethod
    def update(cls, package: list[str] | str):
        """更新依赖包

        参数:
            package: 更新依赖包名称或列表
        """
        if isinstance(package, str):
            package = [package]
        try:
            command = cls.__get_command()
            command.append("install")
            command.append("--upgrade")
            command.append(" ".join(package))
            logger.info(f"执行虚拟环境更新包指令: {command}", LOG_COMMAND)
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.debug(f"更新虚拟环境包指令执行完成: {result.stdout}", LOG_COMMAND)
            return result.stdout
        except CalledProcessError as e:
            logger.error(f"更新虚拟环境包指令执行失败: {e.stderr}.", LOG_COMMAND)
            return e.stderr

    @classmethod
    def install_requirement(cls, requirement_file: Path):
        """安装依赖文件

        参数:
            requirement_file: requirement文件路径

        异常:
            FileNotFoundError: 文件不存在
        """
        if not requirement_file.exists():
            raise FileNotFoundError(f"依赖文件 {requirement_file} 不存在", LOG_COMMAND)
        try:
            command = cls.__get_command()
            command.append("install")
            command.append("-r")
            command.append(str(requirement_file.absolute()))
            logger.info(f"执行虚拟环境安装依赖文件指令: {command}", LOG_COMMAND)
            result = subprocess.run(
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
        except CalledProcessError as e:
            logger.error(
                f"安装虚拟环境依赖文件指令执行失败: {e.stderr}.",
                LOG_COMMAND,
            )
            return e.stderr

    @classmethod
    def list(cls) -> str:
        """列出已安装的依赖包"""
        try:
            command = cls.__get_command()
            command.append("list")
            logger.info(f"执行虚拟环境列出包指令: {command}", LOG_COMMAND)
            result = subprocess.run(
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
        except CalledProcessError as e:
            logger.error(f"列出虚拟环境包指令执行失败: {e.stderr}.", LOG_COMMAND)
        return ""

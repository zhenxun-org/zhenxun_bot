"""
仓库管理工具的工具函数
"""

import asyncio
from pathlib import Path
import re

from zhenxun.services.log import logger

from .config import LOG_COMMAND


async def check_git() -> bool:
    """
    检查环境变量中是否存在 git

    返回:
        bool: 是否存在git命令
    """
    try:
        process = await asyncio.create_subprocess_shell(
            "git --version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return bool(stdout)
    except Exception as e:
        logger.error("检查git命令失败", LOG_COMMAND, e=e)
        return False


async def clean_git(cwd: Path):
    """
    清理git仓库

    参数:
        cwd: 工作目录
    """
    await run_git_command("reset --hard", cwd)
    await run_git_command("clean -xdf", cwd)


async def run_git_command(
    command: str, cwd: Path | None = None
) -> tuple[bool, str, str]:
    """
    运行git命令

    参数:
        command: 命令
        cwd: 工作目录

    返回:
        tuple[bool, str, str]: (是否成功, 标准输出, 标准错误)
    """
    try:
        full_command = f"git {command}"
        # 将Path对象转换为字符串
        cwd_str = str(cwd) if cwd else None
        process = await asyncio.create_subprocess_shell(
            full_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd_str,
        )
        stdout_bytes, stderr_bytes = await process.communicate()

        stdout = stdout_bytes.decode("utf-8").strip()
        stderr = stderr_bytes.decode("utf-8").strip()

        return process.returncode == 0, stdout, stderr
    except Exception as e:
        logger.error(f"运行git命令失败: {command}, 错误: {e}")
        return False, "", str(e)


def glob_to_regex(pattern: str) -> str:
    """
    将glob模式转换为正则表达式

    参数:
        pattern: glob模式，如 "*.py"

    返回:
        str: 正则表达式
    """
    # 转义特殊字符
    regex = re.escape(pattern)

    # 替换glob通配符
    regex = regex.replace(r"\*\*", ".*")  # ** -> .*
    regex = regex.replace(r"\*", "[^/]*")  # * -> [^/]*
    regex = regex.replace(r"\?", "[^/]")  # ? -> [^/]

    # 添加开始和结束标记
    regex = f"^{regex}$"

    return regex


def filter_files(
    files: list[str],
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[str]:
    """
    过滤文件列表

    参数:
        files: 文件列表
        include_patterns: 包含的文件模式列表，如 ["*.py", "docs/*.md"]
        exclude_patterns: 排除的文件模式列表，如 ["__pycache__/*", "*.pyc"]

    返回:
        list[str]: 过滤后的文件列表
    """
    result = files.copy()

    # 应用包含模式
    if include_patterns:
        included = []
        for pattern in include_patterns:
            regex_pattern = glob_to_regex(pattern)
            included.extend(file for file in result if re.match(regex_pattern, file))
        result = included

    # 应用排除模式
    if exclude_patterns:
        for pattern in exclude_patterns:
            regex_pattern = glob_to_regex(pattern)
            result = [file for file in result if not re.match(regex_pattern, file)]

    return result

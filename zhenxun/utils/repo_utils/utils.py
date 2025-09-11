"""
仓库管理工具的工具函数
"""

import asyncio
import base64
from pathlib import Path
import re
import shutil
import tempfile

from zhenxun.services.log import logger

from .config import LOG_COMMAND, RepoConfig
from .exceptions import GitUnavailableError


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


async def sparse_checkout_clone(
    repo_url: str,
    branch: str,
    sparse_path: str,
    target_dir: Path,
) -> None:
    """
    使用 git 稀疏检出克隆指定路径到目标目录（在临时目录中操作）。

    关键保障:
    - 在临时目录中执行所有 git 操作，避免影响 target_dir 中的现有内容
    - 只操作 target_dir/sparse_path 路径，不影响 target_dir 其他内容
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    if not await check_git():
        raise GitUnavailableError()

    # 在临时目录中进行 git 操作
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # 初始化临时目录为 git 仓库
        success, out, err = await run_git_command("init", temp_path)
        if not success:
            raise RuntimeError(f"git init 失败: {err or out}")
        success, out, err = await run_git_command(
            f"remote add origin {repo_url}", temp_path
        )
        if not success:
            raise RuntimeError(f"添加远程失败: {err or out}")

        # 启用稀疏检出（使用 --no-cone 模式以获得更精确的控制）
        await run_git_command("config core.sparseCheckout true", temp_path)
        await run_git_command("sparse-checkout init --no-cone", temp_path)

        # 设置需要检出的路径（每次都覆盖配置）
        if not sparse_path:
            raise RuntimeError("sparse-checkout 路径不能为空")

        # 使用 --no-cone 模式，直接指定要检出的具体路径
        success, out, err = await run_git_command(
            f"sparse-checkout set {sparse_path}/", temp_path
        )
        if not success:
            raise RuntimeError(f"配置稀疏路径失败: {err or out}")

        # 强制拉取并同步到远端
        success, out, err = await run_git_command(
            f"fetch --force --depth 1 origin {branch}", temp_path
        )
        if not success:
            raise RuntimeError(f"fetch 失败: {err or out}")

        # 使用远端强制更新本地分支并覆盖工作区
        success, out, err = await run_git_command(
            f"checkout -B {branch} origin/{branch}", temp_path
        )
        if not success:
            # 回退方案
            success2, out2, err2 = await run_git_command(
                f"checkout {branch}", temp_path
            )
            if not success2:
                raise RuntimeError(f"checkout 失败: {(err or out) or (err2 or out2)}")

        # 强制对齐工作区
        await run_git_command(f"reset --hard origin/{branch}", temp_path)
        await run_git_command("clean -xdf", temp_path)

        # 将检出的文件移动到目标位置
        source_path = temp_path / sparse_path
        if source_path.exists():
            # 确保目标路径存在
            target_path = target_dir / sparse_path
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # 如果目标路径已存在，先清理
            if target_path.exists():
                if target_path.is_dir():
                    shutil.rmtree(target_path)
                else:
                    target_path.unlink()

            # 移动整个目录结构到目标位置
            shutil.move(str(source_path), str(target_path))


def prepare_aliyun_url(repo_url: str) -> str:
    """解析阿里云CodeUp的仓库URL

    参数:
        repo_url: 仓库URL

    返回:
        str: 解析后的仓库URL
    """
    config = RepoConfig.get_instance()

    repo_name = repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")
    # 构建仓库URL
    # 阿里云CodeUp的仓库URL格式通常为：
    # https://codeup.aliyun.com/{organization_id}/{organization_name}/{repo_name}.git
    url = f"https://codeup.aliyun.com/{config.aliyun_codeup.organization_id}/{config.aliyun_codeup.organization_name}/{repo_name}.git"

    # 添加访问令牌 - 使用base64解码后的令牌
    if config.aliyun_codeup.rdc_access_token_encrypted:
        try:
            # 解码RDC访问令牌
            token = base64.b64decode(
                config.aliyun_codeup.rdc_access_token_encrypted.encode()
            ).decode()
            # 阿里云CodeUp使用oauth2:token的格式进行身份验证
            url = url.replace("https://", f"https://oauth2:{token}@")
            logger.debug(f"使用RDC令牌构建阿里云URL: {url.split('@')[0]}@***")
        except Exception as e:
            logger.error(f"解码RDC令牌失败: {e}")

    return url

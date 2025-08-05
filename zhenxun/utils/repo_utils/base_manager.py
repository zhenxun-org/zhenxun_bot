"""
仓库管理工具的基础管理器
"""

from abc import ABC, abstractmethod
from pathlib import Path

import aiofiles

from zhenxun.services.log import logger

from .config import LOG_COMMAND, RepoConfig
from .models import (
    FileDownloadResult,
    RepoCommitInfo,
    RepoFileInfo,
    RepoType,
    RepoUpdateResult,
)
from .utils import check_git, filter_files, run_git_command


class BaseRepoManager(ABC):
    """仓库管理工具基础类"""

    def __init__(self, config: RepoConfig | None = None):
        """
        初始化仓库管理工具

        参数:
            config: 配置，如果为None则使用默认配置
        """
        self.config = config or RepoConfig.get_instance()
        self.config.ensure_dirs()

    @abstractmethod
    async def update_repo(
        self,
        repo_url: str,
        local_path: Path,
        branch: str = "main",
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> RepoUpdateResult:
        """
        更新仓库

        参数:
            repo_url: 仓库URL或名称
            local_path: 本地保存路径
            branch: 分支名称
            include_patterns: 包含的文件模式列表，如 ["*.py", "docs/*.md"]
            exclude_patterns: 排除的文件模式列表，如 ["__pycache__/*", "*.pyc"]

        返回:
            RepoUpdateResult: 更新结果
        """
        pass

    @abstractmethod
    async def download_file(
        self,
        repo_url: str,
        file_path: str,
        local_path: Path,
        branch: str = "main",
    ) -> FileDownloadResult:
        """
        下载单个文件

        参数:
            repo_url: 仓库URL或名称
            file_path: 文件在仓库中的路径
            local_path: 本地保存路径
            branch: 分支名称

        返回:
            FileDownloadResult: 下载结果
        """
        pass

    @abstractmethod
    async def get_file_list(
        self,
        repo_url: str,
        dir_path: str = "",
        branch: str = "main",
        recursive: bool = False,
    ) -> list[RepoFileInfo]:
        """
        获取仓库文件列表

        参数:
            repo_url: 仓库URL或名称
            dir_path: 目录路径，空字符串表示仓库根目录
            branch: 分支名称
            recursive: 是否递归获取子目录

        返回:
            List[RepoFileInfo]: 文件信息列表
        """
        pass

    @abstractmethod
    async def get_commit_info(
        self, repo_url: str, commit_id: str
    ) -> RepoCommitInfo | None:
        """
        获取提交信息

        参数:
            repo_url: 仓库URL或名称
            commit_id: 提交ID

        返回:
            Optional[RepoCommitInfo]: 提交信息，如果获取失败则返回None
        """
        pass

    async def save_file_content(self, content: bytes, local_path: Path) -> int:
        """
        保存文件内容

        参数:
            content: 文件内容
            local_path: 本地保存路径

        返回:
            int: 文件大小（字节）
        """
        # 确保目录存在
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # 保存文件
        async with aiofiles.open(local_path, "wb") as f:
            await f.write(content)

        return len(content)

    async def read_version_file(self, local_dir: Path) -> str:
        """
        读取版本文件

        参数:
            local_dir: 本地目录

        返回:
            str: 版本号
        """
        version_file = local_dir / "__version__"
        if not version_file.exists():
            return ""

        try:
            async with aiofiles.open(version_file) as f:
                return (await f.read()).strip()
        except Exception as e:
            logger.error(f"读取版本文件失败: {e}")
            return ""

    async def write_version_file(self, local_dir: Path, version: str) -> bool:
        """
        写入版本文件

        参数:
            local_dir: 本地目录
            version: 版本号

        返回:
            bool: 是否成功
        """
        version_file = local_dir / "__version__"

        try:
            version_bb = "vNone"
            async with aiofiles.open(version_file) as rf:
                if text := await rf.read():
                    version_bb = text.strip().split("-")[0]
            async with aiofiles.open(version_file, "w") as f:
                await f.write(f"{version_bb}-{version[:6]}")
            return True
        except Exception as e:
            logger.error(f"写入版本文件失败: {e}")
            return False

    def filter_files(
        self,
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
            List[str]: 过滤后的文件列表
        """
        return filter_files(files, include_patterns, exclude_patterns)

    async def update_via_git(
        self,
        repo_url: str,
        local_path: Path,
        branch: str = "main",
        force: bool = False,
        *,
        repo_type: RepoType | None = None,
        owner="",
        prepare_repo_url=None,
    ) -> RepoUpdateResult:
        """
        通过Git命令直接更新仓库

        参数:
            repo_url: 仓库URL或名称
            local_path: 本地仓库路径
            branch: 分支名称
            force: 是否强制拉取
            repo_type: 仓库类型
            owner: 仓库拥有者
            prepare_repo_url: 预处理仓库URL的函数

        返回:
            RepoUpdateResult: 更新结果
        """
        from .models import RepoType

        repo_name = repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")

        try:
            # 创建结果对象
            result = RepoUpdateResult(
                repo_type=repo_type or RepoType.GITHUB,  # 默认使用GitHub类型
                repo_name=repo_name,
                owner=owner or "",
                old_version="",
                new_version="",
            )

            # 检查Git是否可用
            if not await check_git():
                return RepoUpdateResult(
                    repo_type=repo_type or RepoType.GITHUB,
                    repo_name=repo_name,
                    owner=owner or "",
                    old_version="",
                    new_version="",
                    error_message="Git命令不可用",
                )

            # 预处理仓库URL
            if prepare_repo_url:
                repo_url = prepare_repo_url(repo_url)

            # 检查本地目录是否存在
            if not local_path.exists():
                # 如果不存在，则克隆仓库
                logger.info(f"克隆仓库 {repo_url} 到 {local_path}", LOG_COMMAND)
                success, stdout, stderr = await run_git_command(
                    f"clone -b {branch} {repo_url} {local_path}"
                )
                if not success:
                    return RepoUpdateResult(
                        repo_type=repo_type or RepoType.GITHUB,
                        repo_name=repo_name,
                        owner=owner or "",
                        old_version="",
                        new_version="",
                        error_message=f"克隆仓库失败: {stderr}",
                    )

                # 获取当前提交ID
                success, new_version, _ = await run_git_command(
                    "rev-parse HEAD", cwd=local_path
                )
                result.new_version = new_version.strip()
                result.success = True
                return result

            # 如果目录存在，检查是否是Git仓库
            # 首先检查目录本身是否有.git文件夹
            git_dir = local_path / ".git"

            if not git_dir.is_dir():
                # 如果不是Git仓库，尝试初始化它
                logger.info(f"目录 {local_path} 不是Git仓库，尝试初始化", LOG_COMMAND)
                init_success, _, init_stderr = await run_git_command(
                    "init", cwd=local_path
                )
                if not init_success:
                    return RepoUpdateResult(
                        repo_type=repo_type or RepoType.GITHUB,
                        repo_name=repo_name,
                        owner=owner or "",
                        old_version="",
                        new_version="",
                        error_message=f"初始化Git仓库失败: {init_stderr}",
                    )

                # 添加远程仓库
                remote_success, _, remote_stderr = await run_git_command(
                    f"remote add origin {repo_url}", cwd=local_path
                )
                if not remote_success:
                    return RepoUpdateResult(
                        repo_type=repo_type or RepoType.GITHUB,
                        repo_name=repo_name,
                        owner=owner or "",
                        old_version="",
                        new_version="",
                        error_message=f"添加远程仓库失败: {remote_stderr}",
                    )

                logger.info(f"成功初始化Git仓库 {local_path}", LOG_COMMAND)

            # 获取当前提交ID作为旧版本
            success, old_version, _ = await run_git_command(
                "rev-parse HEAD", cwd=local_path
            )
            result.old_version = old_version.strip()

            # 获取当前远程URL
            success, remote_url, _ = await run_git_command(
                "config --get remote.origin.url", cwd=local_path
            )

            # 如果远程URL不匹配，则更新它
            remote_url = remote_url.strip()
            if success and repo_url not in remote_url and remote_url not in repo_url:
                logger.info(f"更新远程URL: {remote_url} -> {repo_url}", LOG_COMMAND)
                await run_git_command(
                    f"remote set-url origin {repo_url}", cwd=local_path
                )

            # 获取远程更新
            logger.info(f"获取远程更新: {repo_url}", LOG_COMMAND)
            success, _, stderr = await run_git_command("fetch origin", cwd=local_path)
            if not success:
                return RepoUpdateResult(
                    repo_type=repo_type or RepoType.GITHUB,
                    repo_name=repo_name,
                    owner=owner or "",
                    old_version=old_version.strip(),
                    new_version="",
                    error_message=f"获取远程更新失败: {stderr}",
                )

            # 获取当前分支
            success, current_branch, _ = await run_git_command(
                "rev-parse --abbrev-ref HEAD", cwd=local_path
            )
            current_branch = current_branch.strip()

            # 如果当前分支不是目标分支，则切换分支
            if success and current_branch != branch:
                logger.info(f"切换分支: {current_branch} -> {branch}", LOG_COMMAND)
                success, _, stderr = await run_git_command(
                    f"checkout {branch}", cwd=local_path
                )
                if not success:
                    return RepoUpdateResult(
                        repo_type=repo_type or RepoType.GITHUB,
                        repo_name=repo_name,
                        owner=owner or "",
                        old_version=old_version.strip(),
                        new_version="",
                        error_message=f"切换分支失败: {stderr}",
                    )

            # 拉取最新代码
            logger.info(f"拉取最新代码: {repo_url}", LOG_COMMAND)
            pull_cmd = f"pull origin {branch}"
            if force:
                pull_cmd = f"fetch --all && git reset --hard origin/{branch}"
                logger.info("使用强制拉取模式", LOG_COMMAND)
            success, _, stderr = await run_git_command(pull_cmd, cwd=local_path)
            if not success:
                return RepoUpdateResult(
                    repo_type=repo_type or RepoType.GITHUB,
                    repo_name=repo_name,
                    owner=owner or "",
                    old_version=old_version.strip(),
                    new_version="",
                    error_message=f"拉取最新代码失败: {stderr}",
                )

            # 获取更新后的提交ID
            success, new_version, _ = await run_git_command(
                "rev-parse HEAD", cwd=local_path
            )
            result.new_version = new_version.strip()

            # 如果版本相同，则无需更新
            if old_version.strip() == new_version.strip():
                logger.info(
                    f"仓库 {repo_url} 已是最新版本: {new_version.strip()}", LOG_COMMAND
                )
                result.success = True
                return result

            # 获取变更的文件列表
            success, changed_files_output, _ = await run_git_command(
                f"diff --name-only {old_version.strip()} {new_version.strip()}",
                cwd=local_path,
            )
            if success:
                changed_files = [
                    line.strip()
                    for line in changed_files_output.splitlines()
                    if line.strip()
                ]
                result.changed_files = changed_files
                logger.info(f"变更的文件列表: {changed_files}", LOG_COMMAND)

            result.success = True
            return result

        except Exception as e:
            logger.error("Git更新失败", LOG_COMMAND, e=e)
            return RepoUpdateResult(
                repo_type=repo_type or RepoType.GITHUB,
                repo_name=repo_name,
                owner=owner or "",
                old_version="",
                new_version="",
                error_message=str(e),
            )

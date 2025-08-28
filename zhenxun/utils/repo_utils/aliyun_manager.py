"""
阿里云CodeUp仓库管理工具
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from aiocache import cached

from zhenxun.services.log import logger
from zhenxun.utils.github_utils.models import AliyunFileInfo
from zhenxun.utils.repo_utils.utils import prepare_aliyun_url

from .base_manager import BaseRepoManager
from .config import LOG_COMMAND, RepoConfig
from .exceptions import (
    AuthenticationError,
    FileNotFoundError,
    RepoDownloadError,
    RepoNotFoundError,
    RepoUpdateError,
)
from .models import (
    FileDownloadResult,
    RepoCommitInfo,
    RepoFileInfo,
    RepoType,
    RepoUpdateResult,
)


class AliyunCodeupManager(BaseRepoManager):
    """阿里云CodeUp仓库管理工具"""

    def __init__(self, config: RepoConfig | None = None):
        """
        初始化阿里云CodeUp仓库管理工具

        Args:
            config: 配置，如果为None则使用默认配置
        """
        super().__init__(config)
        self._client = None

    async def update_repo(
        self,
        repo_url: str,
        local_path: Path,
        branch: str = "main",
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> RepoUpdateResult:
        """
        更新阿里云CodeUp仓库

        Args:
            repo_url: 仓库URL或名称
            local_path: 本地保存路径
            branch: 分支名称
            include_patterns: 包含的文件模式列表，如 ["*.py", "docs/*.md"]
            exclude_patterns: 排除的文件模式列表，如 ["__pycache__/*", "*.pyc"]

        Returns:
            RepoUpdateResult: 更新结果
        """
        try:
            # 检查配置
            self._check_config()

            # 获取仓库名称（从URL中提取）
            repo_url = repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")

            # 获取仓库最新提交ID
            newest_commit = await self._get_newest_commit(repo_url, branch)

            # 创建结果对象
            result = RepoUpdateResult(
                repo_type=RepoType.ALIYUN,
                repo_name=repo_url.split("/tree/")[0]
                .split("/")[-1]
                .replace(".git", ""),
                owner=self.config.aliyun_codeup.organization_id,
                old_version="",  # 将在后面更新
                new_version=newest_commit,
            )
            old_version = await self.read_version_file(local_path)
            result.old_version = old_version

            # 如果版本相同，则无需更新
            if old_version == newest_commit:
                result.success = True
                logger.debug(
                    f"仓库 {repo_url.split('/')[-1].replace('.git', '')}"
                    f" 已是最新版本: {newest_commit[:8]}",
                    LOG_COMMAND,
                )
                return result

            # 确保本地目录存在
            local_path.mkdir(parents=True, exist_ok=True)

            # 获取仓库名称（从URL中提取）
            repo_name = repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")

            # 获取变更的文件列表
            changed_files = await self._get_changed_files(
                repo_name, old_version or None, newest_commit
            )

            # 过滤文件
            if include_patterns or exclude_patterns:
                from .utils import filter_files

                changed_files = filter_files(
                    changed_files, include_patterns, exclude_patterns
                )

            result.changed_files = changed_files

            # 下载变更的文件
            for file_path in changed_files:
                try:
                    local_file_path = local_path / file_path
                    await self._download_file(
                        repo_name, file_path, local_file_path, newest_commit
                    )
                except Exception as e:
                    logger.error(f"下载文件 {file_path} 失败", LOG_COMMAND, e=e)

            # 更新版本文件
            await self.write_version_file(local_path, newest_commit)

            result.success = True
            return result

        except RepoUpdateError as e:
            logger.error(f"更新仓库失败: {e}")
            # 从URL中提取仓库名称
            repo_name = repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")
            return RepoUpdateResult(
                repo_type=RepoType.ALIYUN,
                repo_name=repo_name,
                owner=self.config.aliyun_codeup.organization_id,
                old_version="",
                new_version="",
                error_message=str(e),
            )
        except Exception as e:
            logger.error(f"更新仓库失败: {e}")
            # 从URL中提取仓库名称
            repo_name = repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")
            return RepoUpdateResult(
                repo_type=RepoType.ALIYUN,
                repo_name=repo_name,
                owner=self.config.aliyun_codeup.organization_id,
                old_version="",
                new_version="",
                error_message=str(e),
            )

    async def download_file(
        self,
        repo_url: str,
        file_path: str,
        local_path: Path,
        branch: str = "main",
    ) -> FileDownloadResult:
        """
        从阿里云CodeUp下载单个文件

        Args:
            repo_url: 仓库URL或名称
            file_path: 文件在仓库中的路径
            local_path: 本地保存路径
            branch: 分支名称

        Returns:
            FileDownloadResult: 下载结果
        """
        try:
            # 检查配置
            self._check_config()

            # 获取仓库名称（从URL中提取）
            repo_identifier = (
                repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")
            )

            # 创建结果对象
            result = FileDownloadResult(
                repo_type=RepoType.ALIYUN,
                repo_name=repo_url.split("/tree/")[0]
                .split("/")[-1]
                .replace(".git", ""),
                file_path=file_path,
                version=branch,
            )

            # 确保本地目录存在
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)

            # 下载文件
            file_size = await self._download_file(
                repo_identifier, file_path, local_path, branch
            )

            result.success = True
            result.file_size = file_size
            return result

        except RepoDownloadError as e:
            logger.error(f"下载文件失败: {e}")
            # 从URL中提取仓库名称
            repo_name = repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")
            return FileDownloadResult(
                repo_type=RepoType.ALIYUN,
                repo_name=repo_name,
                file_path=file_path,
                version=branch,
                error_message=str(e),
            )
        except Exception as e:
            logger.error(f"下载文件失败: {e}")
            # 从URL中提取仓库名称
            repo_name = repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")
            return FileDownloadResult(
                repo_type=RepoType.ALIYUN,
                repo_name=repo_name,
                file_path=file_path,
                version=branch,
                error_message=str(e),
            )

    async def get_file_list(
        self,
        repo_url: str,
        dir_path: str = "",
        branch: str = "main",
        recursive: bool = False,
    ) -> list[RepoFileInfo]:
        """
        获取仓库文件列表

        Args:
            repo_url: 仓库URL或名称
            dir_path: 目录路径，空字符串表示仓库根目录
            branch: 分支名称
            recursive: 是否递归获取子目录

        Returns:
            list[RepoFileInfo]: 文件信息列表
        """
        try:
            # 检查配置
            self._check_config()

            # 获取仓库名称（从URL中提取）
            repo_identifier = (
                repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")
            )

            # 获取文件列表
            search_type = "RECURSIVE" if recursive else "DIRECT"
            tree_list = await AliyunFileInfo.get_repository_tree(
                repo_identifier, dir_path, branch, search_type
            )

            result = []
            for tree in tree_list:
                # 跳过非当前目录的文件（如果不是递归模式）
                if (
                    not recursive
                    and tree.path != dir_path
                    and "/" in tree.path.replace(dir_path, "", 1).strip("/")
                ):
                    continue

                file_info = RepoFileInfo(
                    path=tree.path,
                    is_dir=tree.type == "tree",
                )
                result.append(file_info)

            return result

        except Exception as e:
            logger.error(f"获取文件列表失败: {e}")
            return []

    async def get_commit_info(
        self, repo_url: str, commit_id: str
    ) -> RepoCommitInfo | None:
        """
        获取提交信息

        Args:
            repo_url: 仓库URL或名称
            commit_id: 提交ID

        Returns:
            Optional[RepoCommitInfo]: 提交信息，如果获取失败则返回None
        """
        try:
            # 检查配置
            self._check_config()

            # 获取仓库名称（从URL中提取）
            repo_identifier = (
                repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "")
            )

            # 获取提交信息
            # 注意：这里假设AliyunFileInfo有get_commit_info方法，如果没有，需要实现
            commit_data = await self._get_commit_info(repo_identifier, commit_id)

            if not commit_data:
                return None

            # 解析提交信息
            id_value = commit_data.get("id", commit_id)
            message_value = commit_data.get("message", "")
            author_value = commit_data.get("author_name", "")
            date_value = commit_data.get(
                "authored_date", datetime.now().isoformat()
            ).replace("Z", "+00:00")

            return RepoCommitInfo(
                commit_id=id_value,
                message=message_value,
                author=author_value,
                commit_time=datetime.fromisoformat(date_value),
                changed_files=[],  # 阿里云API可能没有直接提供变更文件列表
            )
        except Exception as e:
            logger.error(f"获取提交信息失败: {e}")
            return None

    def _check_config(self):
        """检查配置"""
        if not self.config.aliyun_codeup.access_key_id:
            raise AuthenticationError("阿里云CodeUp")

        if not self.config.aliyun_codeup.access_key_secret:
            raise AuthenticationError("阿里云CodeUp")

        if not self.config.aliyun_codeup.organization_id:
            raise AuthenticationError("阿里云CodeUp")

    async def _get_newest_commit(self, repo_name: str, branch: str) -> str:
        """
        获取仓库最新提交ID

        Args:
            repo_name: 仓库名称
            branch: 分支名称

        Returns:
            str: 提交ID
        """
        try:
            newest_commit = await AliyunFileInfo.get_newest_commit(repo_name, branch)
            if not newest_commit:
                raise RepoNotFoundError(repo_name)
            return newest_commit
        except Exception as e:
            logger.error(f"获取最新提交ID失败: {e}")
            raise RepoUpdateError(f"获取最新提交ID失败: {e}")

    async def _get_commit_info(self, repo_name: str, commit_id: str) -> dict:
        """
        获取提交信息

        Args:
            repo_name: 仓库名称
            commit_id: 提交ID

        Returns:
            dict: 提交信息
        """
        # 这里需要实现从阿里云获取提交信息的逻辑
        # 由于AliyunFileInfo可能没有get_commit_info方法，这里提供一个简单的实现
        try:
            # 这里应该是调用阿里云API获取提交信息
            # 这里只是一个示例，实际上需要根据阿里云API实现
            return {
                "id": commit_id,
                "message": "提交信息",
                "author_name": "作者",
                "authored_date": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"获取提交信息失败: {e}")
            return {}

    @cached(ttl=3600)
    async def _get_changed_files(
        self, repo_name: str, old_commit: str | None, new_commit: str
    ) -> list[str]:
        """
        获取两个提交之间变更的文件列表

        Args:
            repo_name: 仓库名称
            old_commit: 旧提交ID，如果为None则获取所有文件
            new_commit: 新提交ID

        Returns:
            list[str]: 变更的文件列表
        """
        if not old_commit:
            # 如果没有旧提交，则获取仓库中的所有文件
            tree_list = await AliyunFileInfo.get_repository_tree(
                repo_name, "", new_commit, "RECURSIVE"
            )
            return [tree.path for tree in tree_list if tree.type == "blob"]

        # 获取两个提交之间的差异
        try:
            return []
        except Exception as e:
            logger.error(f"获取提交差异失败: {e}")
            raise RepoUpdateError(f"获取提交差异失败: {e}")

    async def update_via_git(
        self,
        repo_url: str,
        local_path: Path,
        branch: str = "main",
        force: bool = False,
        *,
        repo_type: RepoType | None = None,
        owner: str | None = None,
        prepare_repo_url: Callable[[str], str] | None = None,
    ) -> RepoUpdateResult:
        """
        通过Git命令直接更新仓库

        参数:
            repo_url: 仓库名称
            local_path: 本地仓库路径
            branch: 分支名称
            force: 是否强制拉取

        返回:
            RepoUpdateResult: 更新结果
        """
        # 调用基类的update_via_git方法
        return await super().update_via_git(
            repo_url=repo_url,
            local_path=local_path,
            branch=branch,
            force=force,
            repo_type=RepoType.ALIYUN,
            owner=self.config.aliyun_codeup.organization_id,
            prepare_repo_url=prepare_aliyun_url,
        )

    async def update(
        self,
        repo_url: str,
        local_path: Path,
        branch: str = "main",
        use_git: bool = True,
        force: bool = False,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> RepoUpdateResult:
        """
        更新仓库，可选择使用Git命令或API方式

        参数:
            repo_url: 仓库名称
            local_path: 本地保存路径
            branch: 分支名称
            use_git: 是否使用Git命令更新
            include_patterns: 包含的文件模式列表，如 ["*.py", "docs/*.md"]
            exclude_patterns: 排除的文件模式列表，如 ["__pycache__/*", "*.pyc"]

        返回:
            RepoUpdateResult: 更新结果
        """
        if use_git:
            return await self.update_via_git(repo_url, local_path, branch, force)
        else:
            return await self.update_repo(
                repo_url, local_path, branch, include_patterns, exclude_patterns
            )

    async def _download_file(
        self, repo_name: str, file_path: str, local_path: Path, ref: str
    ) -> int:
        """
        下载文件

        Args:
            repo_name: 仓库名称
            file_path: 文件在仓库中的路径
            local_path: 本地保存路径
            ref: 分支/标签/提交ID

        Returns:
            int: 文件大小（字节）
        """
        # 确保目录存在
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # 获取文件内容
        for retry in range(self.config.aliyun_codeup.download_retry + 1):
            try:
                content = await AliyunFileInfo.get_file_content(
                    file_path, repo_name, ref
                )

                if content is None:
                    raise FileNotFoundError(file_path, repo_name)

                # 保存文件
                return await self.save_file_content(content.encode("utf-8"), local_path)

            except FileNotFoundError as e:
                # 这些错误不需要重试
                raise e
            except Exception as e:
                if retry < self.config.aliyun_codeup.download_retry:
                    logger.warning("下载文件失败，将重试", LOG_COMMAND, e=e)
                    await asyncio.sleep(1)
                    continue
                raise RepoDownloadError(f"下载文件失败: {e}")

        raise RepoDownloadError("下载文件失败: 超过最大重试次数")

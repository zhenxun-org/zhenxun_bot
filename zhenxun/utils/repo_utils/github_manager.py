"""
GitHub仓库管理工具
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from aiocache import cached

from zhenxun.services.log import logger
from zhenxun.utils.github_utils import GithubUtils, RepoInfo
from zhenxun.utils.http_utils import AsyncHttpx

from .base_manager import BaseRepoManager
from .config import LOG_COMMAND, RepoConfig
from .exceptions import (
    ApiRateLimitError,
    FileNotFoundError,
    NetworkError,
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


class GithubManager(BaseRepoManager):
    """GitHub仓库管理工具"""

    def __init__(self, config: RepoConfig | None = None):
        """
        初始化GitHub仓库管理工具

        参数:
            config: 配置，如果为None则使用默认配置
        """
        super().__init__(config)

    async def update_repo(
        self,
        repo_url: str,
        local_path: Path,
        branch: str = "main",
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> RepoUpdateResult:
        """
        更新GitHub仓库

        参数:
            repo_url: 仓库URL，格式为 https://github.com/owner/repo
            local_path: 本地保存路径
            branch: 分支名称
            include_patterns: 包含的文件模式列表，如 ["*.py", "docs/*.md"]
            exclude_patterns: 排除的文件模式列表，如 ["__pycache__/*", "*.pyc"]

        返回:
            RepoUpdateResult: 更新结果
        """
        try:
            # 解析仓库URL
            repo_info = GithubUtils.parse_github_url(repo_url)
            repo_info.branch = branch

            # 获取仓库最新提交ID
            newest_commit = await self._get_newest_commit(
                repo_info.owner, repo_info.repo, branch
            )

            # 创建结果对象
            result = RepoUpdateResult(
                repo_type=RepoType.GITHUB,
                repo_name=repo_info.repo,
                owner=repo_info.owner,
                old_version="",  # 将在后面更新
                new_version=newest_commit,
            )

            old_version = await self.read_version_file(local_path)
            old_version = old_version.split("-")[-1]
            result.old_version = old_version

            # 如果版本相同，则无需更新
            if newest_commit in old_version:
                result.success = True
                logger.debug(
                    f"仓库 {repo_info.repo} 已是最新版本: {newest_commit}",
                    LOG_COMMAND,
                )
                return result

            # 确保本地目录存在
            local_path.mkdir(parents=True, exist_ok=True)

            # 获取变更的文件列表
            changed_files = await self._get_changed_files(
                repo_info.owner,
                repo_info.repo,
                old_version or None,
                newest_commit,
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
                    await self._download_file(repo_info, file_path, local_file_path)
                except Exception as e:
                    logger.error(f"下载文件 {file_path} 失败", LOG_COMMAND, e=e)

            # 更新版本文件
            await self.write_version_file(local_path, newest_commit)

            result.success = True
            return result

        except RepoUpdateError as e:
            logger.error("更新仓库失败", LOG_COMMAND, e=e)
            return RepoUpdateResult(
                repo_type=RepoType.GITHUB,
                repo_name=repo_url.split("/")[-1] if "/" in repo_url else repo_url,
                owner=repo_url.split("/")[-2] if "/" in repo_url else "unknown",
                old_version="",
                new_version="",
                error_message=str(e),
            )
        except Exception as e:
            logger.error("更新仓库失败", LOG_COMMAND, e=e)
            return RepoUpdateResult(
                repo_type=RepoType.GITHUB,
                repo_name=repo_url.split("/")[-1] if "/" in repo_url else repo_url,
                owner=repo_url.split("/")[-2] if "/" in repo_url else "unknown",
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
        从GitHub下载单个文件

        参数:
            repo_url: 仓库URL，格式为 https://github.com/owner/repo
            file_path: 文件在仓库中的路径
            local_path: 本地保存路径
            branch: 分支名称

        返回:
            FileDownloadResult: 下载结果
        """
        repo_name = (
            repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "").strip()
        )
        try:
            # 解析仓库URL
            repo_info = GithubUtils.parse_github_url(repo_url)
            repo_info.branch = branch

            # 创建结果对象
            result = FileDownloadResult(
                repo_type=RepoType.GITHUB,
                repo_name=repo_info.repo,
                file_path=file_path,
                version=branch,
            )

            # 确保本地目录存在
            local_path.parent.mkdir(parents=True, exist_ok=True)

            # 下载文件
            file_size = await self._download_file(repo_info, file_path, local_path)

            result.success = True
            result.file_size = file_size
            return result

        except RepoDownloadError as e:
            logger.error("下载文件失败", LOG_COMMAND, e=e)
            return FileDownloadResult(
                repo_type=RepoType.GITHUB,
                repo_name=repo_name,
                file_path=file_path,
                version=branch,
                error_message=str(e),
            )
        except Exception as e:
            logger.error("下载文件失败", LOG_COMMAND, e=e)
            return FileDownloadResult(
                repo_type=RepoType.GITHUB,
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

        参数:
            repo_url: 仓库URL，格式为 https://github.com/owner/repo
            dir_path: 目录路径，空字符串表示仓库根目录
            branch: 分支名称
            recursive: 是否递归获取子目录

        返回:
            list[RepoFileInfo]: 文件信息列表
        """
        try:
            # 解析仓库URL
            repo_info = GithubUtils.parse_github_url(repo_url)
            repo_info.branch = branch

            # 获取文件列表
            for api in GithubUtils.iter_api_strategies():
                try:
                    await api.parse_repo_info(repo_info)
                    files = api.get_files(dir_path, True)

                    result = []
                    for file_path in files:
                        # 跳过非当前目录的文件（如果不是递归模式）
                        if not recursive and "/" in file_path.replace(
                            dir_path, "", 1
                        ).strip("/"):
                            continue

                        is_dir = file_path.endswith("/")
                        file_info = RepoFileInfo(path=file_path, is_dir=is_dir)
                        result.append(file_info)

                    return result
                except Exception as e:
                    logger.debug("使用API策略获取文件列表失败", LOG_COMMAND, e=e)
                    continue

            raise RepoNotFoundError(repo_url)

        except Exception as e:
            logger.error("获取文件列表失败", LOG_COMMAND, e=e)
            return []

    async def get_commit_info(
        self, repo_url: str, commit_id: str
    ) -> RepoCommitInfo | None:
        """
        获取提交信息

        参数:
            repo_url: 仓库URL，格式为 https://github.com/owner/repo
            commit_id: 提交ID

        返回:
            Optional[RepoCommitInfo]: 提交信息，如果获取失败则返回None
        """
        try:
            # 解析仓库URL
            repo_info = GithubUtils.parse_github_url(repo_url)

            # 构建API URL
            api_url = f"https://api.github.com/repos/{repo_info.owner}/{repo_info.repo}/commits/{commit_id}"

            # 发送请求
            resp = await AsyncHttpx.get(
                api_url,
                timeout=self.config.github.api_timeout,
                proxy=self.config.github.proxy,
            )

            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                raise ApiRateLimitError("GitHub")

            if resp.status_code != 200:
                if resp.status_code == 404:
                    raise RepoNotFoundError(f"{repo_info.owner}/{repo_info.repo}")
                raise NetworkError(f"HTTP {resp.status_code}: {resp.text}")

            data = resp.json()

            return RepoCommitInfo(
                commit_id=data["sha"],
                message=data["commit"]["message"],
                author=data["commit"]["author"]["name"],
                commit_time=datetime.fromisoformat(
                    data["commit"]["author"]["date"].replace("Z", "+00:00")
                ),
                changed_files=[file["filename"] for file in data.get("files", [])],
            )
        except Exception as e:
            logger.error("获取提交信息失败", LOG_COMMAND, e=e)
            return None

    async def _get_newest_commit(self, owner: str, repo: str, branch: str) -> str:
        """
        获取仓库最新提交ID

        参数:
            owner: 仓库拥有者
            repo: 仓库名称
            branch: 分支名称

        返回:
            str: 提交ID
        """
        try:
            newest_commit = await RepoInfo.get_newest_commit(owner, repo, branch)
            if not newest_commit:
                raise RepoNotFoundError(f"{owner}/{repo}")
            return newest_commit
        except Exception as e:
            logger.error("获取最新提交ID失败", LOG_COMMAND, e=e)
            raise RepoUpdateError(f"获取最新提交ID失败: {e}")

    @cached(ttl=3600)
    async def _get_changed_files(
        self, owner: str, repo: str, old_commit: str | None, new_commit: str
    ) -> list[str]:
        """
        获取两个提交之间变更的文件列表

        参数:
            owner: 仓库拥有者
            repo: 仓库名称
            old_commit: 旧提交ID，如果为None则获取所有文件
            new_commit: 新提交ID

        返回:
            list[str]: 变更的文件列表
        """
        if not old_commit:
            # 如果没有旧提交，则获取仓库中的所有文件
            api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{new_commit}?recursive=1"

            resp = await AsyncHttpx.get(
                api_url,
                timeout=self.config.github.api_timeout,
                proxy=self.config.github.proxy,
            )

            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                raise ApiRateLimitError("GitHub")

            if resp.status_code != 200:
                if resp.status_code == 404:
                    raise RepoNotFoundError(f"{owner}/{repo}")
                raise NetworkError(f"HTTP {resp.status_code}: {resp.text}")

            data = resp.json()
            return [
                item["path"] for item in data.get("tree", []) if item["type"] == "blob"
            ]

        # 如果有旧提交，则获取两个提交之间的差异
        api_url = f"https://api.github.com/repos/{owner}/{repo}/compare/{old_commit}...{new_commit}"

        resp = await AsyncHttpx.get(
            api_url,
            timeout=self.config.github.api_timeout,
            proxy=self.config.github.proxy,
        )

        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            raise ApiRateLimitError("GitHub")

        if resp.status_code != 200:
            if resp.status_code == 404:
                raise RepoNotFoundError(f"{owner}/{repo}")
            raise NetworkError(f"HTTP {resp.status_code}: {resp.text}")

        data = resp.json()
        return [file["filename"] for file in data.get("files", [])]

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
            repo_url: 仓库URL，格式为 https://github.com/owner/repo
            local_path: 本地仓库路径
            branch: 分支名称
            force: 是否强制拉取

        返回:
            RepoUpdateResult: 更新结果
        """
        # 解析仓库URL
        repo_info = GithubUtils.parse_github_url(repo_url)

        # 调用基类的update_via_git方法
        return await super().update_via_git(
            repo_url=repo_url,
            local_path=local_path,
            branch=branch,
            force=force,
            repo_type=RepoType.GITHUB,
            owner=repo_info.owner,
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
            repo_url: 仓库URL，格式为 https://github.com/owner/repo
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
        self, repo_info: RepoInfo, file_path: str, local_path: Path
    ) -> int:
        """
        下载文件

        参数:
            repo_info: 仓库信息
            file_path: 文件在仓库中的路径
            local_path: 本地保存路径

        返回:
            int: 文件大小（字节）
        """
        # 确保目录存在
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # 获取下载URL
        download_url = await repo_info.get_raw_download_url(file_path)

        # 下载文件
        for retry in range(self.config.github.download_retry + 1):
            try:
                resp = await AsyncHttpx.get(
                    download_url,
                    timeout=self.config.github.download_timeout,
                )

                if resp.status_code == 403 and "rate limit" in resp.text.lower():
                    raise ApiRateLimitError("GitHub")

                if resp.status_code != 200:
                    if resp.status_code == 404:
                        raise FileNotFoundError(
                            file_path, f"{repo_info.owner}/{repo_info.repo}"
                        )

                    if retry < self.config.github.download_retry:
                        await asyncio.sleep(1)
                        continue

                    raise NetworkError(f"HTTP {resp.status_code}: {resp.text}")

                # 保存文件
                return await self.save_file_content(resp.content, local_path)

            except (ApiRateLimitError, FileNotFoundError) as e:
                # 这些错误不需要重试
                raise e
            except Exception as e:
                if retry < self.config.github.download_retry:
                    logger.warning("下载文件失败，将重试", LOG_COMMAND, e=e)
                    await asyncio.sleep(1)
                    continue
                raise RepoDownloadError("下载文件失败")

        raise RepoDownloadError("下载文件失败: 超过最大重试次数")

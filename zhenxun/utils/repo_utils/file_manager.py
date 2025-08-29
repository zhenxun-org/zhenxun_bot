"""
仓库文件管理器，用于从GitHub和阿里云CodeUp获取指定文件内容
"""

import contextlib
from pathlib import Path
from typing import cast, overload

import aiofiles
from httpx import Response

from zhenxun.services.log import logger
from zhenxun.utils.github_utils import GithubUtils
from zhenxun.utils.github_utils.models import AliyunTreeType, GitHubStrategy, TreeType
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.utils import is_binary_file

from .config import LOG_COMMAND, RepoConfig
from .exceptions import (
    FileNotFoundError,
    GitUnavailableError,
    NetworkError,
    RepoManagerError,
)
from .models import FileDownloadResult, RepoFileInfo, RepoType
from .utils import prepare_aliyun_url, sparse_checkout_clone


class RepoFileManager:
    """仓库文件管理器，用于获取GitHub和阿里云仓库中的文件内容"""

    def __init__(self, config: RepoConfig | None = None):
        """
        初始化仓库文件管理器

        参数:
            config: 配置，如果为None则使用默认配置
        """
        self.config = config or RepoConfig.get_instance()
        self.config.ensure_dirs()

    @overload
    async def get_github_file_content(
        self, url: str, file_path: str, ignore_error: bool = False
    ) -> str: ...

    @overload
    async def get_github_file_content(
        self, url: str, file_path: list[str], ignore_error: bool = False
    ) -> list[tuple[str, str]]: ...

    async def get_github_file_content(
        self, url: str, file_path: str | list[str], ignore_error: bool = False
    ) -> str | list[tuple[str, str]]:
        """
        获取GitHub仓库文件内容

        参数:
            url: 仓库URL
            file_path: 文件路径或文件路径列表
            ignore_error: 是否忽略错误

        返回:
            list[tuple[str, str]]: 文件路径，文件内容
        """
        results = []
        is_str_input = isinstance(file_path, str)
        try:
            if is_str_input:
                file_path = [file_path]
            repo_info = GithubUtils.parse_github_url(url)
            if await repo_info.update_repo_commit():
                logger.info(f"获取最新提交: {repo_info.branch}", LOG_COMMAND)
            else:
                logger.warning(f"获取最新提交失败: {repo_info}", LOG_COMMAND)
            for f in file_path:
                try:
                    file_url = await repo_info.get_raw_download_urls(f)
                    for fu in file_url:
                        response: Response = await AsyncHttpx.get(
                            fu, check_status_code=200
                        )
                        if response.status_code == 200:
                            logger.info(f"获取github文件内容成功: {f}", LOG_COMMAND)
                            text_content = response.content
                            # 确保使用UTF-8编码解析响应内容
                            if not is_binary_file(f):
                                try:
                                    text_content = response.content.decode("utf-8")
                                except UnicodeDecodeError:
                                    # 如果UTF-8解码失败，尝试其他编码
                                    text_content = response.content.decode(
                                        "utf-8", errors="ignore"
                                    )
                                    logger.warning(
                                        f"解码文件内容时出现错误，使用忽略错误模式:{f}",
                                        LOG_COMMAND,
                                    )
                            results.append((f, text_content))
                            break
                        else:
                            logger.warning(
                                f"获取github文件内容失败: {response.status_code}",
                                LOG_COMMAND,
                            )
                except Exception as e:
                    logger.warning(f"获取github文件内容失败: {f}", LOG_COMMAND, e=e)
                    if not ignore_error:
                        raise
        except Exception as e:
            logger.error(f"获取GitHub文件内容失败: {file_path}", LOG_COMMAND, e=e)
            raise
        logger.debug(f"获取GitHub文件内容: {[r[0] for r in results]}", LOG_COMMAND)

        return results[0][1] if is_str_input and results else results

    @overload
    async def get_aliyun_file_content(
        self,
        repo_name: str,
        file_path: str,
        branch: str = "main",
        ignore_error: bool = False,
    ) -> str: ...

    @overload
    async def get_aliyun_file_content(
        self,
        repo_name: str,
        file_path: list[str],
        branch: str = "main",
        ignore_error: bool = False,
    ) -> list[tuple[str, str]]: ...

    async def get_aliyun_file_content(
        self,
        repo_name: str,
        file_path: str | list[str],
        branch: str = "main",
        ignore_error: bool = False,
    ) -> str | list[tuple[str, str]]:
        """
        获取阿里云CodeUp仓库文件内容

        参数:
            repo: 仓库名称
            file_path: 文件路径
            branch: 分支名称
            ignore_error: 是否忽略错误
        返回:
            list[tuple[str, str]]: 文件路径，文件内容
        """
        results = []
        is_str_input = isinstance(file_path, str)
        # 导入阿里云相关模块
        from zhenxun.utils.github_utils.models import AliyunFileInfo

        if is_str_input:
            file_path = [file_path]
        for f in file_path:
            try:
                content = await AliyunFileInfo.get_file_content(
                    file_path=f, repo=repo_name, ref=branch
                )
                results.append((f, content))
            except Exception as e:
                if "code: 404" not in str(e):
                    logger.warning(
                        f"获取阿里云文件内容失败: {file_path}", LOG_COMMAND, e=e
                    )
                if not ignore_error:
                    raise
        logger.debug(f"获取阿里云文件内容: {[r[0] for r in results]}", LOG_COMMAND)
        return results[0][1] if is_str_input and results else results

    @overload
    async def get_file_content(
        self,
        repo_url: str,
        file_path: str,
        branch: str = "main",
        repo_type: RepoType | None = None,
        ignore_error: bool = False,
    ) -> str: ...

    @overload
    async def get_file_content(
        self,
        repo_url: str,
        file_path: list[str],
        branch: str = "main",
        repo_type: RepoType | None = None,
        ignore_error: bool = False,
    ) -> list[tuple[str, str]]: ...

    async def get_file_content(
        self,
        repo_url: str,
        file_path: str | list[str],
        branch: str = "main",
        repo_type: RepoType | None = None,
        ignore_error: bool = False,
    ) -> str | list[tuple[str, str]]:
        """
        获取仓库文件内容

        参数:
            repo_url: 仓库URL
            file_path: 文件路径
            branch: 分支名称
            repo_type: 仓库类型，如果为None则自动判断
            ignore_error: 是否忽略错误

        返回:
            str: 文件内容
        """
        # 确定仓库类型
        repo_name = (
            repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "").strip()
        )
        if repo_type is None:
            try:
                return await self.get_aliyun_file_content(
                    repo_name, file_path, branch, ignore_error
                )
            except Exception:
                return await self.get_github_file_content(
                    repo_url, file_path, ignore_error
                )

        try:
            if repo_type == RepoType.GITHUB:
                return await self.get_github_file_content(
                    repo_url, file_path, ignore_error
                )

            elif repo_type == RepoType.ALIYUN:
                return await self.get_aliyun_file_content(
                    repo_name, file_path, branch, ignore_error
                )

        except Exception as e:
            if isinstance(e, FileNotFoundError | NetworkError | RepoManagerError):
                raise
            raise RepoManagerError(f"获取文件内容失败: {e}")

    async def list_directory_files(
        self,
        repo_url: str,
        directory_path: str = "",
        branch: str = "main",
        repo_type: RepoType | None = None,
        recursive: bool = True,
    ) -> list[RepoFileInfo]:
        """
        获取仓库目录下的所有文件路径

        参数:
            repo_url: 仓库URL
            directory_path: 目录路径，默认为仓库根目录
            branch: 分支名称
            repo_type: 仓库类型，如果为None则自动判断
            recursive: 是否递归获取子目录文件

        返回:
            list[RepoFileInfo]: 文件信息列表
        """
        repo_name = (
            repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "").strip()
        )
        try:
            if repo_type is None:
                # 尝试GitHub，失败则尝试阿里云
                try:
                    return await self._list_aliyun_directory_files(
                        repo_name, directory_path, branch, recursive
                    )
                except Exception as e:
                    logger.warning(
                        "获取阿里云目录文件失败，尝试GitHub", LOG_COMMAND, e=e
                    )
                    return await self._list_github_directory_files(
                        repo_url, directory_path, branch, recursive
                    )
            if repo_type == RepoType.GITHUB:
                return await self._list_github_directory_files(
                    repo_url, directory_path, branch, recursive
                )
            elif repo_type == RepoType.ALIYUN:
                return await self._list_aliyun_directory_files(
                    repo_name, directory_path, branch, recursive
                )
        except Exception as e:
            logger.error(f"获取目录文件列表失败: {directory_path}", LOG_COMMAND, e=e)
            if isinstance(e, FileNotFoundError | NetworkError | RepoManagerError):
                raise
            raise RepoManagerError(f"获取目录文件列表失败: {e}")

    async def _list_github_directory_files(
        self,
        repo_url: str,
        directory_path: str = "",
        branch: str = "main",
        recursive: bool = True,
        build_tree: bool = False,
    ) -> list[RepoFileInfo]:
        """
        获取GitHub仓库目录下的所有文件路径

        参数:
            repo_url: 仓库URL
            directory_path: 目录路径，默认为仓库根目录
            branch: 分支名称
            recursive: 是否递归获取子目录文件
            build_tree: 是否构建目录树

        返回:
            list[RepoFileInfo]: 文件信息列表
        """
        try:
            repo_info = GithubUtils.parse_github_url(repo_url)
            if await repo_info.update_repo_commit():
                logger.info(f"获取最新提交: {repo_info.branch}", LOG_COMMAND)
            else:
                logger.warning(f"获取最新提交失败: {repo_info}", LOG_COMMAND)

            # 获取仓库树信息
            strategy = GitHubStrategy()
            strategy.body = await GitHubStrategy.parse_repo_info(repo_info)

            # 处理目录路径，确保格式正确
            if directory_path and not directory_path.endswith("/") and recursive:
                directory_path = f"{directory_path}/"

            # 获取文件列表
            file_list = []
            for tree_item in strategy.body.tree:
                # 如果不是递归模式，只获取当前目录下的文件
                if not recursive and "/" in tree_item.path.replace(
                    directory_path, "", 1
                ):
                    continue

                # 检查是否在指定目录下
                if directory_path and not tree_item.path.startswith(directory_path):
                    continue

                # 创建文件信息对象
                file_info = RepoFileInfo(
                    path=tree_item.path,
                    is_dir=tree_item.type == TreeType.DIR,
                    size=tree_item.size,
                    last_modified=None,  # GitHub API不直接提供最后修改时间
                )
                file_list.append(file_info)

            # 构建目录树结构
            if recursive and build_tree:
                file_list = self._build_directory_tree(file_list)

            return file_list

        except Exception as e:
            logger.error(
                f"获取GitHub目录文件列表失败: {directory_path}", LOG_COMMAND, e=e
            )
            raise

    async def _list_aliyun_directory_files(
        self,
        repo_name: str,
        directory_path: str = "",
        branch: str = "main",
        recursive: bool = True,
        build_tree: bool = False,
    ) -> list[RepoFileInfo]:
        """
        获取阿里云CodeUp仓库目录下的所有文件路径

        参数:
            repo_name: 仓库名称
            directory_path: 目录路径，默认为仓库根目录
            branch: 分支名称
            recursive: 是否递归获取子目录文件
            build_tree: 是否构建目录树

        返回:
            list[RepoFileInfo]: 文件信息列表
        """
        try:
            from zhenxun.utils.github_utils.models import AliyunFileInfo

            # 获取仓库树信息
            search_type = "RECURSIVE" if recursive else "DIRECT"
            tree_list = await AliyunFileInfo.get_repository_tree(
                repo=repo_name,
                path=directory_path,
                ref=branch,
                search_type=search_type,
            )

            # 创建文件信息对象列表
            file_list = []
            for tree_item in tree_list:
                file_info = RepoFileInfo(
                    path=tree_item.path,
                    is_dir=tree_item.type == AliyunTreeType.DIR,
                    size=None,  # 阿里云API不直接提供文件大小
                    last_modified=None,  # 阿里云API不直接提供最后修改时间
                )
                file_list.append(file_info)

            # 构建目录树结构
            if recursive and build_tree:
                file_list = self._build_directory_tree(file_list)

            return file_list

        except Exception as e:
            logger.error(
                f"获取阿里云目录文件列表失败: {directory_path}", LOG_COMMAND, e=e
            )
            raise

    def _build_directory_tree(
        self, file_list: list[RepoFileInfo]
    ) -> list[RepoFileInfo]:
        """
        构建目录树结构

        参数:
            file_list: 文件信息列表

        返回:
            list[RepoFileInfo]: 根目录下的文件信息列表
        """
        # 按路径排序，确保父目录在子目录之前
        file_list.sort(key=lambda x: x.path)
        # 创建路径到文件信息的映射
        path_map = {file_info.path: file_info for file_info in file_list}
        # 根目录文件列表
        root_files = []

        for file_info in file_list:
            if parent_path := "/".join(file_info.path.split("/")[:-1]):
                # 如果有父目录，将当前文件添加到父目录的子文件列表中
                if parent_path in path_map:
                    path_map[parent_path].children.append(file_info)
                else:
                    # 如果父目录不在列表中，创建一个虚拟的父目录
                    parent_info = RepoFileInfo(
                        path=parent_path, is_dir=True, children=[file_info]
                    )
                    path_map[parent_path] = parent_info
                    # 检查父目录的父目录
                    grand_parent_path = "/".join(parent_path.split("/")[:-1])
                    if grand_parent_path and grand_parent_path in path_map:
                        path_map[grand_parent_path].children.append(parent_info)
                    else:
                        root_files.append(parent_info)
            else:
                # 如果没有父目录，则是根目录下的文件
                root_files.append(file_info)

        # 返回根目录下的文件列表
        return [
            file
            for file in root_files
            if all(f.path != file.path for f in file_list if f != file)
        ]

    async def download_files(
        self,
        repo_url: str,
        file_path: tuple[str, Path] | list[tuple[str, Path]],
        branch: str = "main",
        repo_type: RepoType | None = None,
        ignore_error: bool = False,
        sparse_path: str | None = None,
        target_dir: Path | None = None,
    ) -> FileDownloadResult:
        """
        下载单个文件

        参数:
            repo_url: 仓库URL
            file_path: 文件在仓库中的路径，本地存储路径
            branch: 分支名称
            repo_type: 仓库类型，如果为None则自动判断
            ignore_error: 是否忽略错误
            sparse_path: 稀疏检出路径
            target_dir: 稀疏目标目录

        返回:
            FileDownloadResult: 下载结果
        """

        # 参数一致性校验：sparse_path 与 target_dir 必须同时有值或同时为 None
        if (sparse_path is None) ^ (target_dir is None):
            raise RepoManagerError(
                "参数错误: sparse_path 与 target_dir 必须同时提供或同时为 None"
            )

        # 确定仓库类型和所有者
        repo_name = (
            repo_url.split("/tree/")[0].split("/")[-1].replace(".git", "").strip()
        )

        if isinstance(file_path, tuple):
            file_path = [file_path]

        file_path_mapping = {f[0]: f[1] for f in file_path}

        # 创建结果对象
        result = FileDownloadResult(
            repo_type=repo_type,
            repo_name=repo_name,
            file_path=file_path,
            version=branch,
        )
        if (
            any(is_binary_file(file_name) for file_name in file_path_mapping)
            and repo_type != RepoType.GITHUB
            and sparse_path
            and target_dir
        ):
            return await self._handle_binary_with_sparse_checkout(
                repo_url=repo_url,
                branch=branch,
                sparse_path=sparse_path,
                target_dir=target_dir,
                result=result,
            )
        else:
            # 不包含二进制时
            return await self._download_and_write_files(
                repo_url=repo_url,
                file_paths=[f[0] for f in file_path],
                file_path_mapping=file_path_mapping,
                branch=branch,
                repo_type=repo_type,
                ignore_error=ignore_error,
                result=result,
            )

    async def _download_and_write_files(
        self,
        repo_url: str,
        file_paths: list[str],
        file_path_mapping: dict[str, Path],
        branch: str,
        repo_type: RepoType | None,
        ignore_error: bool,
        result: FileDownloadResult,
    ) -> FileDownloadResult:
        try:
            if len(file_paths) == 1:
                file_contents_result = await self.get_file_content(
                    repo_url, file_paths[0], branch, repo_type, ignore_error
                )
                if isinstance(file_contents_result, tuple):
                    file_contents = [file_contents_result]
                elif isinstance(file_contents_result, str):
                    file_contents = [(file_paths[0], file_contents_result)]
                else:
                    file_contents = cast(list[tuple[str, str]], file_contents_result)
            else:
                file_contents = cast(
                    list[tuple[str, str]],
                    await self.get_file_content(
                        repo_url, file_paths, branch, repo_type, ignore_error
                    ),
                )

            for repo_file_path, content in file_contents:
                local_path = file_path_mapping[repo_file_path]
                local_path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, str):
                    content_bytes = content.encode("utf-8")
                else:
                    content_bytes = content
                logger.debug(f"写入文件: {local_path}")
                async with aiofiles.open(local_path, "wb") as f:
                    await f.write(content_bytes)
            result.success = True
            result.file_size = sum(
                len(content.encode("utf-8") if isinstance(content, str) else content)
                for _, content in file_contents
            )
            logger.info(f"下载文件成功: {[f[0] for f in file_contents]}")
            return result
        except Exception as e:
            logger.error(f"下载文件失败: {e}")
            result.success = False
            result.error_message = str(e)
            return result

    async def _handle_binary_with_sparse_checkout(
        self,
        repo_url: str,
        branch: str,
        sparse_path: str,
        target_dir: Path,
        result: FileDownloadResult,
    ) -> FileDownloadResult:
        try:
            await sparse_checkout_clone(
                repo_url=prepare_aliyun_url(repo_url),
                branch=branch,
                sparse_path=sparse_path,
                target_dir=target_dir,
            )
            total_size = 0
            if target_dir.exists():
                for f in target_dir.rglob("*"):
                    if f.is_file():
                        with contextlib.suppress(Exception):
                            total_size += f.stat().st_size
            result.success = True
            result.file_size = total_size
            logger.info(f"sparse-checkout 克隆成功: {target_dir}")
            return result
        except GitUnavailableError as e:
            logger.error(f"Git不可用: {e}")
            result.success = False
            result.error_message = (
                "当前插件包含二进制文件，因ali限制需要使用git，"
                "当前Git不可用，请尝试添加参数 -s git 或 安装 git"
            )
            return result
        except Exception as e:
            logger.error(f"sparse-checkout 克隆失败: {e}")
            result.success = False
            result.error_message = str(e)
            return result

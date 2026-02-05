import base64
import contextlib
import sys
from typing import ClassVar, Protocol

from aiocache import cached
from alibabacloud_devops20210625 import models as devops_20210625_models
from alibabacloud_devops20210625.client import Client as devops20210625Client
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models
from nonebot.compat import model_dump
from pydantic import BaseModel, Field

from zhenxun.utils.http_utils import AsyncHttpx

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from strenum import StrEnum

from .const import (
    ALIYUN_ENDPOINT,
    ALIYUN_EXTERNAL_PLUGIN_GROUPS,
    ALIYUN_ORG_ID,
    ALIYUN_REGION,
    ALIYUN_REPO_MAPPING,
    CACHED_API_TTL,
    GIT_API_COMMIT_FORMAT,
    GIT_API_PROXY_COMMIT_FORMAT,
    GIT_API_TREES_FORMAT,
    JSD_PACKAGE_API_FORMAT,
    Aliyun_AccessKey_ID,
    Aliyun_Secret_AccessKey_encrypted,
    RDC_access_token_encrypted,
)
from .func import (
    get_fastest_archive_formats,
    get_fastest_raw_formats,
    get_fastest_release_source_formats,
)


class RepoInfo(BaseModel):
    """仓库信息"""

    owner: str
    repo: str
    branch: str = "main"

    async def get_raw_download_url(self, path: str) -> str:
        return (await self.get_raw_download_urls(path))[0]

    async def get_archive_download_url(self) -> str:
        return (await self.get_archive_download_urls())[0]

    async def get_release_source_download_url_tgz(self, version: str) -> str:
        return (await self.get_release_source_download_urls_tgz(version))[0]

    async def get_release_source_download_url_zip(self, version: str) -> str:
        return (await self.get_release_source_download_urls_zip(version))[0]

    async def get_raw_download_urls(self, path: str) -> list[str]:
        url_formats = await get_fastest_raw_formats()
        return [
            url_format.format(**self.to_dict(), path=path) for url_format in url_formats
        ]

    async def get_archive_download_urls(self) -> list[str]:
        url_formats = await get_fastest_archive_formats()
        return [url_format.format(**self.to_dict()) for url_format in url_formats]

    async def get_release_source_download_urls_tgz(self, version: str) -> list[str]:
        url_formats = await get_fastest_release_source_formats()
        return [
            url_format.format(**self.to_dict(), version=version, compress="tar.gz")
            for url_format in url_formats
        ]

    async def get_release_source_download_urls_zip(self, version: str) -> list[str]:
        url_formats = await get_fastest_release_source_formats()
        return [
            url_format.format(**self.to_dict(), version=version, compress="zip")
            for url_format in url_formats
        ]

    async def update_repo_commit(self):
        with contextlib.suppress(Exception):
            newest_commit = await self.get_newest_commit(
                self.owner, self.repo, self.branch
            )
            if newest_commit:
                self.branch = newest_commit
                return True
        return False

    def to_dict(self, **kwargs):
        return model_dump(self, **kwargs)

    @classmethod
    @cached(ttl=CACHED_API_TTL)
    async def get_newest_commit(cls, owner: str, repo: str, branch: str) -> str:
        commit_url = GIT_API_COMMIT_FORMAT.format(owner=owner, repo=repo, branch=branch)
        commit_url_proxy = GIT_API_PROXY_COMMIT_FORMAT.format(
            owner=owner, repo=repo, branch=branch
        )
        resp = await AsyncHttpx().get([commit_url, commit_url_proxy])
        return "" if resp.status_code != 200 else resp.json()["sha"]


class APIStrategy(Protocol):
    """API策略"""

    body: BaseModel

    async def parse_repo_info(self, repo_info: RepoInfo) -> BaseModel: ...

    def get_files(self, module_path: str, is_dir: bool) -> list[str]: ...


class RepoAPI:
    """基础接口"""

    def __init__(self, strategy: APIStrategy):
        self.strategy = strategy

    async def parse_repo_info(self, repo_info: RepoInfo):
        body = await self.strategy.parse_repo_info(repo_info)
        self.strategy.body = body

    def get_files(self, module_path: str, is_dir: bool) -> list[str]:
        return self.strategy.get_files(module_path, is_dir)


class FileType(StrEnum):
    """文件类型"""

    FILE = "file"
    DIR = "directory"
    PACKAGE = "gh"


class FileInfo(BaseModel):
    """文件信息"""

    type: FileType
    name: str
    files: list["FileInfo"] = Field(default_factory=list)


class JsdelivrStrategy:
    """Jsdelivr策略"""

    body: FileInfo

    def get_file_paths(self, module_path: str, is_dir: bool = True) -> list[str]:
        """获取文件路径"""
        paths = module_path.split("/")
        filename = "" if is_dir and module_path else paths[-1]
        paths = paths if is_dir and module_path else paths[:-1]
        cur_file = self.body
        for path in paths:  # 导航到正确的目录
            cur_file = next(
                (
                    f
                    for f in cur_file.files
                    if f.type == FileType.DIR and f.name == path
                ),
                None,
            )
            if not cur_file:
                raise ValueError(f"模块路径{module_path}不存在")

        def collect_files(file: FileInfo, current_path: str, filename: str):
            """收集文件"""
            if file.type == FileType.FILE and (not filename or file.name == filename):
                return [f"{current_path}/{file.name}"]
            elif file.type == FileType.DIR and file.files:
                return [
                    path
                    for f in file.files
                    for path in collect_files(
                        f,
                        (
                            f"{current_path}/{f.name}"
                            if f.type == FileType.DIR
                            else current_path
                        ),
                        filename,
                    )
                ]
            return []

        files = collect_files(cur_file, "/".join(paths), filename)
        return files if module_path else [f[1:] for f in files]

    @classmethod
    @cached(ttl=CACHED_API_TTL)
    async def parse_repo_info(cls, repo_info: RepoInfo) -> "FileInfo":
        """解析仓库信息"""

        """获取插件包信息

        参数:
            repo_info: 仓库信息

        返回:
            FileInfo: 插件包信息
        """
        jsd_package_url: str = JSD_PACKAGE_API_FORMAT.format(
            owner=repo_info.owner, repo=repo_info.repo, branch=repo_info.branch
        )
        res = await AsyncHttpx.get(url=jsd_package_url)
        if res.status_code != 200:
            raise ValueError(f"下载错误, code: {res.status_code}")
        return FileInfo(**res.json())

    def get_files(self, module_path: str, is_dir: bool = True) -> list[str]:
        """获取文件路径"""
        return self.get_file_paths(module_path, is_dir)


class TreeType(StrEnum):
    """树类型"""

    FILE = "blob"
    DIR = "tree"


class Tree(BaseModel):
    """树"""

    path: str
    mode: str
    type: TreeType
    sha: str
    size: int | None = None
    url: str


class TreeInfo(BaseModel):
    """树信息"""

    sha: str
    url: str
    tree: list[Tree]


class GitHubStrategy:
    """GitHub策略"""

    body: TreeInfo

    def export_files(self, module_path: str, is_dir: bool) -> list[str]:
        """导出文件路径"""
        tree_info = self.body
        return [
            file.path
            for file in tree_info.tree
            if file.type == TreeType.FILE
            and file.path.startswith(module_path)
            and (not is_dir or file.path[len(module_path)] == "/" or not module_path)
        ]

    @classmethod
    @cached(ttl=CACHED_API_TTL)
    async def parse_repo_info(cls, repo_info: RepoInfo) -> "TreeInfo":
        """获取仓库树

        参数:
            repo_info: 仓库信息

        返回:
            TreesInfo: 仓库树信息
        """
        git_tree_url: str = GIT_API_TREES_FORMAT.format(
            owner=repo_info.owner, repo=repo_info.repo, branch=repo_info.branch
        )
        res = await AsyncHttpx.get(url=git_tree_url)
        if res.status_code != 200:
            raise ValueError(f"下载错误, code: {res.status_code}")
        return TreeInfo(**res.json())

    def get_files(self, module_path: str, is_dir: bool = True) -> list[str]:
        """获取文件路径"""
        return self.export_files(module_path, is_dir)


class AliyunTreeType(StrEnum):
    """阿里云树类型"""

    FILE = "blob"
    DIR = "tree"


class AliyunTree(BaseModel):
    """阿里云树节点"""

    id: str
    is_lfs: bool = Field(alias="isLFS", default=False)
    mode: str
    name: str
    path: str
    type: AliyunTreeType

    class Config:
        populate_by_name = True


class AliyunFileInfo:
    """阿里云策略"""

    content: str
    """文件内容"""
    file_path: str
    """文件路径"""
    ref: str
    """分支/标签/提交版本"""
    repository_id: str
    """仓库ID"""

    # 动态仓库ID缓存: {repo_name: repository_id}
    _dynamic_repo_cache: ClassVar[dict[str, str]] = {}

    @classmethod
    async def get_client(cls) -> devops20210625Client:
        """获取阿里云客户端"""
        config = open_api_models.Config(
            access_key_id=Aliyun_AccessKey_ID,
            access_key_secret=base64.b64decode(
                Aliyun_Secret_AccessKey_encrypted.encode()
            ).decode(),
            endpoint=ALIYUN_ENDPOINT,
            region_id=ALIYUN_REGION,
        )

        return devops20210625Client(config)

    @classmethod
    @cached(ttl=CACHED_API_TTL)
    async def list_group_repositories(cls, group_path: str) -> list[dict]:
        """列出分组下的所有仓库

        参数:
            group_path: 分组路径，如 "zhenxun_plugins"

        返回:
            list[dict]: 仓库信息列表，每个元素包含 id, name, path 等
        """
        try:
            client = await cls.get_client()
            result = []
            page = 1
            per_page = 100

            while True:
                request = devops_20210625_models.ListRepositoriesRequest(
                    organization_id=ALIYUN_ORG_ID,
                    access_token=base64.b64decode(
                        RDC_access_token_encrypted.encode()
                    ).decode(),
                    page=page,
                    per_page=per_page,
                )

                runtime = util_models.RuntimeOptions()
                headers = {}

                response = await client.list_repositories_with_options_async(
                    request, headers, runtime
                )

                if response and response.body:
                    if not response.body.success:
                        raise ValueError(
                            f"阿里云请求失败: {response.body.error_code} - "
                            f"{response.body.error_message}"
                        )

                    repos = response.body.result or []
                    if not repos:
                        break

                    for repo in repos:
                        repo_dict = repo.to_map()
                        # 尝试多种可能的字段名
                        path_with_ns = (
                            repo_dict.get("pathWithNamespace")
                            or repo_dict.get("path_with_namespace")
                            or repo_dict.get("path")
                            or ""
                        )
                        # pathWithNamespace 格式: {org_id}/{group_path}/{repo_name}
                        # 只保留属于该分组的仓库
                        if f"/{group_path}/" in path_with_ns:
                            result.append(repo_dict)
                            # 同时更新缓存
                            repo_name = repo_dict.get("name", "")
                            # 注意：阿里云API返回的ID字段是 "Id" (大写I)
                            repo_id = str(
                                repo_dict.get("Id") or repo_dict.get("id") or ""
                            )
                            if repo_name and repo_id:
                                cls._dynamic_repo_cache[repo_name] = repo_id

                    # 如果返回的数量小于请求的数量，说明已经没有更多数据
                    if len(repos) < per_page:
                        break
                    page += 1
                else:
                    break

            return result
        except Exception as e:
            raise ValueError(f"获取仓库列表失败: {e}")

    @classmethod
    async def get_repository_id(cls, repo_name: str) -> str | None:
        """通过仓库名称获取仓库ID

        优先从静态映射中查找，然后从缓存中查找，最后从API动态查询

        参数:
            repo_name: 仓库名称

        返回:
            str | None: 仓库ID，未找到返回 None
        """
        # 1. 先从静态映射中查找
        if repo_id := ALIYUN_REPO_MAPPING.get(repo_name):
            return repo_id

        # 2. 从动态缓存中查找
        if repo_id := cls._dynamic_repo_cache.get(repo_name):
            return repo_id

        # 3. 从外部插件分组中动态查询
        for group_path in ALIYUN_EXTERNAL_PLUGIN_GROUPS:
            try:
                repos = await cls.list_group_repositories(group_path)
                for repo in repos:
                    if repo.get("name") == repo_name:
                        # 注意：阿里云API返回的ID字段是 "Id" (大写I)
                        repo_id = str(repo.get("Id") or repo.get("id") or "")
                        if repo_id:
                            cls._dynamic_repo_cache[repo_name] = repo_id
                            return repo_id
            except Exception:
                continue

        return None

    @classmethod
    async def debug_list_all_repositories(cls) -> list[dict]:
        """调试方法：列出组织下的所有仓库信息

        返回:
            list[dict]: 所有仓库信息列表
        """
        try:
            client = await cls.get_client()
            result = []

            request = devops_20210625_models.ListRepositoriesRequest(
                organization_id=ALIYUN_ORG_ID,
                access_token=base64.b64decode(
                    RDC_access_token_encrypted.encode()
                ).decode(),
                page=1,
                per_page=100,
            )

            runtime = util_models.RuntimeOptions()
            headers = {}

            response = await client.list_repositories_with_options_async(
                request, headers, runtime
            )

            if response and response.body and response.body.result:
                for repo in response.body.result:
                    repo_dict = repo.to_map()
                    result.append(repo_dict)
                    # 同时填充缓存
                    repo_name = repo_dict.get("name", "")
                    repo_id = str(repo_dict.get("Id") or repo_dict.get("id") or "")
                    if repo_name and repo_id:
                        cls._dynamic_repo_cache[repo_name] = repo_id

            return result
        except Exception as e:
            raise ValueError(f"列出仓库失败: {e}")

    @classmethod
    async def _get_repository_id_or_raise(cls, repo: str) -> str:
        """获取仓库ID，如果未找到则抛出异常

        参数:
            repo: 仓库名称

        返回:
            str: 仓库ID

        异常:
            ValueError: 未找到仓库
        """
        repository_id = await cls.get_repository_id(repo)
        if not repository_id:
            raise ValueError(f"未找到仓库 {repo} 对应的阿里云仓库ID")
        return repository_id

    @classmethod
    async def get_file_content(
        cls, file_path: str, repo: str, ref: str = "main"
    ) -> str:
        """获取文件内容

        参数:
            file_path: 文件路径
            repo: 仓库名称
            ref: 分支名称/标签名称/提交版本号

        返回:
            str: 文件内容
        """
        try:
            repository_id = await cls._get_repository_id_or_raise(repo)

            client = await cls.get_client()

            request = devops_20210625_models.GetFileBlobsRequest(
                organization_id=ALIYUN_ORG_ID,
                file_path=file_path,
                ref=ref,
                access_token=base64.b64decode(
                    RDC_access_token_encrypted.encode()
                ).decode(),
            )

            runtime = util_models.RuntimeOptions()
            headers = {}

            response = await client.get_file_blobs_with_options_async(
                repository_id,
                request,
                headers,
                runtime,
            )

            if response and response.body and response.body.result:
                if not response.body.success:
                    raise ValueError(
                        f"阿里云请求失败: {response.body.error_code} - "
                        f"{response.body.error_message}"
                    )
                return response.body.result.content or ""

            raise ValueError("获取阿里云文件内容失败")
        except Exception as e:
            raise ValueError(f"获取阿里云文件内容失败: {e}")

    @classmethod
    async def get_repository_tree(
        cls,
        repo: str,
        path: str = "",
        ref: str = "main",
        search_type: str = "DIRECT",
    ) -> list[AliyunTree]:
        """获取仓库树信息

        参数:
            repo: 仓库名称
            path: 代码仓库内的文件路径
            ref: 分支名称/标签名称/提交版本
            search_type: 查找策略
            "DIRECT"  # 仅展示当前目录下的内容
            "RECURSIVE"  # 递归查找当前路径下的所有文件
            "FLATTEN"  # 扁平化展示

        返回:
            list[AliyunTree]: 仓库树信息列表
        """
        try:
            repository_id = await cls._get_repository_id_or_raise(repo)

            client = await cls.get_client()

            request = devops_20210625_models.ListRepositoryTreeRequest(
                organization_id=ALIYUN_ORG_ID,
                path=path,
                access_token=base64.b64decode(
                    RDC_access_token_encrypted.encode()
                ).decode(),
                ref_name=ref,
                type=search_type,
            )

            runtime = util_models.RuntimeOptions()
            headers = {}

            response = await client.list_repository_tree_with_options_async(
                repository_id, request, headers, runtime
            )

            if response and response.body:
                if not response.body.success:
                    raise ValueError(
                        f"阿里云请求失败: {response.body.error_code} - "
                        f"{response.body.error_message}"
                    )
                return [
                    AliyunTree(**item.to_map()) for item in (response.body.result or [])
                ]
            raise ValueError("获取仓库树信息失败")
        except Exception as e:
            raise ValueError(f"获取仓库树信息失败: {e}")

    @classmethod
    async def get_newest_commit(cls, repo: str, branch: str = "main") -> str:
        """获取最新提交
        参数:
            repo: 仓库名称
            branch: sha 分支名称/标签名称/提交版本号
        返回:
            commit: 最新提交信息
        """
        try:
            repository_id = await cls._get_repository_id_or_raise(repo)

            client = await cls.get_client()

            request = devops_20210625_models.GetRepositoryCommitRequest(
                organization_id=ALIYUN_ORG_ID,
                access_token=base64.b64decode(
                    RDC_access_token_encrypted.encode()
                ).decode(),
            )

            runtime = util_models.RuntimeOptions()
            headers = {}

            response = await client.get_repository_commit_with_options_async(
                repository_id, branch, request, headers, runtime
            )

            if response and response.body:
                if not response.body.success:
                    raise ValueError(
                        f"阿里云请求失败: {response.body.error_code} - "
                        f"{response.body.error_message}"
                    )
                return response.body.result.id or ""
            raise ValueError("获取仓库commit信息失败")
        except Exception as e:
            raise ValueError(f"获取仓库commit信息失败: {e}")

    def export_files(
        self, tree_list: list[AliyunTree], module_path: str, is_dir: bool
    ) -> list[str]:
        """导出文件路径"""
        return [
            file.path
            for file in tree_list
            if file.type == AliyunTreeType.FILE
            and file.path.startswith(module_path)
            and (not is_dir or file.path[len(module_path)] == "/" or not module_path)
        ]

    @classmethod
    async def parse_repo_info(cls, repo: str) -> list[str]:
        """解析仓库信息获取仓库树"""
        # 验证仓库存在
        await cls._get_repository_id_or_raise(repo)

        tree_list = await cls.get_repository_tree(
            repo=repo,
        )
        return cls().export_files(tree_list, "", True)

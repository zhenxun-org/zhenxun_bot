"""
仓库管理工具的数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class RepoType(str, Enum):
    """仓库类型"""

    GITHUB = "github"
    ALIYUN = "aliyun"


@dataclass
class RepoFileInfo:
    """仓库文件信息"""

    # 文件路径
    path: str
    # 是否是目录
    is_dir: bool
    # 文件大小（字节）
    size: int | None = None
    # 最后修改时间
    last_modified: datetime | None = None
    # 子文件列表
    children: list["RepoFileInfo"] = field(default_factory=list)


@dataclass
class RepoCommitInfo:
    """仓库提交信息"""

    # 提交ID
    commit_id: str
    # 提交消息
    message: str
    # 作者
    author: str
    # 提交时间
    commit_time: datetime
    # 变更的文件列表
    changed_files: list[str] = field(default_factory=list)


@dataclass
class RepoUpdateResult:
    """仓库更新结果"""

    # 仓库类型
    repo_type: RepoType
    # 仓库名称
    repo_name: str
    # 仓库拥有者
    owner: str
    # 旧版本
    old_version: str
    # 新版本
    new_version: str
    # 是否成功
    success: bool = False
    # 错误消息
    error_message: str = ""
    # 变更的文件列表
    changed_files: list[str] = field(default_factory=list)


@dataclass
class FileDownloadResult:
    """文件下载结果"""

    # 仓库类型
    repo_type: RepoType | None
    # 仓库名称
    repo_name: str
    # 文件路径
    file_path: list[tuple[str, Path]] | str
    # 版本
    version: str
    # 是否成功
    success: bool = False
    # 文件大小（字节）
    file_size: int = 0
    # 错误消息
    error_message: str = ""

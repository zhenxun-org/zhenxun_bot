"""
仓库管理工具，用于操作GitHub和阿里云CodeUp项目的更新和文件下载
"""

from .aliyun_manager import AliyunCodeupManager
from .base_manager import BaseRepoManager
from .config import AliyunCodeupConfig, GithubConfig, RepoConfig
from .exceptions import (
    ApiRateLimitError,
    AuthenticationError,
    ConfigError,
    FileNotFoundError,
    NetworkError,
    RepoDownloadError,
    RepoManagerError,
    RepoNotFoundError,
    RepoUpdateError,
)
from .file_manager import RepoFileManager as RepoFileManagerClass
from .github_manager import GithubManager
from .models import (
    FileDownloadResult,
    RepoCommitInfo,
    RepoFileInfo,
    RepoType,
    RepoUpdateResult,
)
from .utils import check_git, filter_files, glob_to_regex, run_git_command

GithubRepoManager = GithubManager()
AliyunRepoManager = AliyunCodeupManager()
RepoFileManager = RepoFileManagerClass()

__all__ = [
    "AliyunCodeupConfig",
    "AliyunRepoManager",
    "ApiRateLimitError",
    "AuthenticationError",
    "BaseRepoManager",
    "ConfigError",
    "FileDownloadResult",
    "FileNotFoundError",
    "GithubConfig",
    "GithubRepoManager",
    "NetworkError",
    "RepoCommitInfo",
    "RepoConfig",
    "RepoDownloadError",
    "RepoFileInfo",
    "RepoFileManager",
    "RepoManagerError",
    "RepoNotFoundError",
    "RepoType",
    "RepoUpdateError",
    "RepoUpdateResult",
    "check_git",
    "filter_files",
    "glob_to_regex",
    "run_git_command",
]

"""
仓库管理工具的异常类
"""


class RepoManagerError(Exception):
    """仓库管理工具异常基类"""

    def __init__(self, message: str, repo_name: str | None = None):
        self.message = message
        self.repo_name = repo_name
        super().__init__(self.message)


class RepoUpdateError(RepoManagerError):
    """仓库更新异常"""

    def __init__(self, message: str, repo_name: str | None = None):
        super().__init__(f"仓库更新失败: {message}", repo_name)


class RepoDownloadError(RepoManagerError):
    """仓库下载异常"""

    def __init__(self, message: str, repo_name: str | None = None):
        super().__init__(f"文件下载失败: {message}", repo_name)


class RepoNotFoundError(RepoManagerError):
    """仓库不存在异常"""

    def __init__(self, repo_name: str):
        super().__init__(f"仓库不存在: {repo_name}", repo_name)


class FileNotFoundError(RepoManagerError):
    """文件不存在异常"""

    def __init__(self, file_path: str, repo_name: str | None = None):
        super().__init__(f"文件不存在: {file_path}", repo_name)


class AuthenticationError(RepoManagerError):
    """认证异常"""

    def __init__(self, repo_type: str):
        super().__init__(f"认证失败: {repo_type}")


class ApiRateLimitError(RepoManagerError):
    """API速率限制异常"""

    def __init__(self, repo_type: str):
        super().__init__(f"API速率限制: {repo_type}")


class NetworkError(RepoManagerError):
    """网络异常"""

    def __init__(self, message: str):
        super().__init__(f"网络错误: {message}")


class ConfigError(RepoManagerError):
    """配置异常"""

    def __init__(self, message: str):
        super().__init__(f"配置错误: {message}")


class GitUnavailableError(RepoManagerError):
    """Git不可用异常"""

    def __init__(self, message: str = "Git命令不可用"):
        super().__init__(message)

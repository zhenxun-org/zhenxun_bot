"""
仓库管理工具的配置模块
"""

from dataclasses import dataclass, field
from pathlib import Path

from zhenxun.configs.path_config import TEMP_PATH

LOG_COMMAND = "RepoUtils"


@dataclass
class GithubConfig:
    """GitHub配置"""

    # API超时时间（秒）
    api_timeout: int = 30
    # 下载超时时间（秒）
    download_timeout: int = 60
    # 下载重试次数
    download_retry: int = 3
    # 代理配置
    proxy: str | None = None


@dataclass
class AliyunCodeupConfig:
    """阿里云CodeUp配置"""

    # 访问密钥ID
    access_key_id: str = "LTAI5tNmf7KaTAuhcvRobAQs"
    # 访问密钥密钥
    access_key_secret: str = "NmJ3d2VNRU1MREY0T1RtRnBqMlFqdlBxN3pMUk1j"
    # 组织ID
    organization_id: str = "67a361cf556e6cdab537117a"
    # 组织名称
    organization_name: str = "zhenxun-org"
    # RDC Access Token
    rdc_access_token_encrypted: str = (
        "cHQtYXp0allnQWpub0FYZWpqZm1RWGtneHk0XzBlMmYzZTZmLWQwOWItNDE4Mi1iZWUx"
        "LTQ1ZTFkYjI0NGRlMg=="
    )
    # 区域
    region: str = "cn-hangzhou"
    # 端点
    endpoint: str = "devops.cn-hangzhou.aliyuncs.com"
    # 下载重试次数
    download_retry: int = 3


@dataclass
class RepoConfig:
    """仓库管理工具配置"""

    # 缓存目录
    cache_dir: Path = TEMP_PATH / "repo_cache"

    # GitHub配置
    github: GithubConfig = field(default_factory=GithubConfig)

    # 阿里云CodeUp配置
    aliyun_codeup: AliyunCodeupConfig = field(default_factory=AliyunCodeupConfig)

    # 单例实例
    _instance = None

    @classmethod
    def get_instance(cls) -> "RepoConfig":
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def ensure_dirs(self):
        """确保目录存在"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)

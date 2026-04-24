"""
头像缓存服务

提供一个统一的、带缓存的头像获取服务，支持多平台和可配置的过期策略。
"""

from collections import OrderedDict
import os
from pathlib import Path
import time

from nonebot_plugin_apscheduler import scheduler

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import DATA_PATH
from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.platform import PlatformUtils

Config.add_plugin_config(
    "avatar_cache",
    "ENABLED",
    True,
    help="是否启用头像缓存功能",
    default_value=True,
    type=bool,
)
Config.add_plugin_config(
    "avatar_cache",
    "TTL_DAYS",
    7,
    help="头像缓存的有效期（天）",
    default_value=7,
    type=int,
)
Config.add_plugin_config(
    "avatar_cache",
    "CLEANUP_INTERVAL_HOURS",
    24,
    help="后台清理过期缓存的间隔时间（小时）",
    default_value=24,
    type=int,
)


class AvatarService:
    """
    一个集中式的头像缓存服务，提供L1（内存）和L2（文件）两级缓存。
    """

    _MEMORY_CACHE_MAX_ITEMS = 2000

    def __init__(self):
        self.cache_path = (DATA_PATH / "cache" / "avatars").resolve()
        self.cache_path.mkdir(parents=True, exist_ok=True)
        self._memory_cache: OrderedDict[str, Path] = OrderedDict()

    def _get_cache_path(self, platform: str, identifier: str) -> Path:
        """
        根据平台和ID生成存储的文件路径。
        例如: data/cache/avatars/qq/123456789.png
        """
        identifier = str(identifier)
        return self.cache_path / platform / f"{identifier}.png"

    def clear_memory_cache(self) -> int:
        size = len(self._memory_cache)
        self._memory_cache.clear()
        return size

    async def get_avatar_path(
        self, platform: str, identifier: str, force_refresh: bool = False
    ) -> Path | None:
        """
        获取用户或群组的头像本地路径。

        参数:
            platform: 平台名称 (e.g., 'qq')
            identifier: 用户ID或群组ID
            force_refresh: 是否强制刷新缓存

        返回:
            Path | None: 头像的本地文件路径，如果获取失败则返回None。
        """
        if not Config.get_config("avatar_cache", "ENABLED"):
            return None

        cache_key = f"{platform}-{identifier}"
        if not force_refresh and cache_key in self._memory_cache:
            cached_path = self._memory_cache[cache_key]
            if cached_path.exists():
                self._memory_cache.move_to_end(cache_key)
                return cached_path
            self._memory_cache.pop(cache_key, None)

        local_path = self._get_cache_path(platform, identifier)
        ttl_seconds = Config.get_config("avatar_cache", "TTL_DAYS", 7) * 86400

        if not force_refresh and local_path.exists():
            try:
                file_mtime = os.path.getmtime(local_path)
                if time.time() - file_mtime < ttl_seconds:
                    self._memory_cache[cache_key] = local_path
                    self._memory_cache.move_to_end(cache_key)
                    while len(self._memory_cache) > self._MEMORY_CACHE_MAX_ITEMS:
                        self._memory_cache.popitem(last=False)
                    return local_path
            except FileNotFoundError:
                pass

        avatar_url = PlatformUtils.get_user_avatar_url(identifier, platform)
        if not avatar_url:
            return None

        local_path.parent.mkdir(parents=True, exist_ok=True)

        if await AsyncHttpx.download_file(avatar_url, local_path):
            self._memory_cache[cache_key] = local_path
            self._memory_cache.move_to_end(cache_key)
            while len(self._memory_cache) > self._MEMORY_CACHE_MAX_ITEMS:
                self._memory_cache.popitem(last=False)
            return local_path
        else:
            logger.warning(f"下载头像失败: {avatar_url}", "AvatarService")
            return None

    async def _cleanup_cache(self):
        """后台定时清理过期的缓存文件"""
        if not Config.get_config("avatar_cache", "ENABLED"):
            return

        logger.info("开始执行头像缓存清理任务...", "AvatarService")
        ttl_seconds = Config.get_config("avatar_cache", "TTL_DAYS", 7) * 86400
        now = time.time()
        deleted_count = 0
        for root, _, files in os.walk(self.cache_path):
            for name in files:
                file_path = Path(root) / name
                try:
                    if now - os.path.getmtime(file_path) > ttl_seconds:
                        file_path.unlink()
                        deleted_count += 1
                except FileNotFoundError:
                    continue

        if self._memory_cache:
            stale_keys = []
            for key, cached_path in self._memory_cache.items():
                if not cached_path.exists():
                    stale_keys.append(key)
                    continue
                try:
                    if now - os.path.getmtime(cached_path) > ttl_seconds:
                        stale_keys.append(key)
                except OSError:
                    stale_keys.append(key)
            for key in stale_keys:
                self._memory_cache.pop(key, None)
            while len(self._memory_cache) > self._MEMORY_CACHE_MAX_ITEMS:
                self._memory_cache.popitem(last=False)

        logger.info(
            f"头像缓存清理完成，共删除 {deleted_count} 个过期文件。", "AvatarService"
        )


avatar_service = AvatarService()


@scheduler.scheduled_job(
    "cron",
    hour=4,
    minute=30,
)
async def _run_avatar_cache_cleanup():
    await avatar_service._cleanup_cache()

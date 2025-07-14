"""
缓存初始化模块

负责注册各种缓存类型，实现按需缓存机制
"""

from zhenxun.models.ban_console import BanConsole
from zhenxun.models.bot_console import BotConsole
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.level_user import LevelUser
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.user_console import UserConsole
from zhenxun.services.cache import CacheRegistry, cache_config
from zhenxun.services.cache.config import CacheMode
from zhenxun.services.log import logger
from zhenxun.utils.enum import CacheType


# 注册缓存类型
def register_cache_types():
    """注册所有缓存类型"""
    CacheRegistry.register(CacheType.PLUGINS, PluginInfo)
    CacheRegistry.register(CacheType.GROUPS, GroupConsole)
    CacheRegistry.register(CacheType.BOT, BotConsole)
    CacheRegistry.register(CacheType.USERS, UserConsole)
    CacheRegistry.register(
        CacheType.LEVEL, LevelUser, key_format="{user_id}_{group_id}"
    )
    CacheRegistry.register(CacheType.BAN, BanConsole, key_format="{user_id}_{group_id}")

    if cache_config.cache_mode == CacheMode.NONE:
        logger.info("缓存功能已禁用，将直接从数据库获取数据")
    else:
        logger.info(f"已注册所有缓存类型，缓存模式: {cache_config.cache_mode}")
        logger.info("使用增量缓存模式，数据将按需加载到缓存中")

"""
缓存系统配置
"""

# 日志标识
LOG_COMMAND = "CacheRoot"

# 默认缓存过期时间（秒）
DEFAULT_EXPIRE = 600

# 缓存键前缀
CACHE_KEY_PREFIX = "ZHENXUN"

# 缓存键分隔符
CACHE_KEY_SEPARATOR = ":"

# 复合键分隔符（用于分隔tuple类型的cache_key_field）
COMPOSITE_KEY_SEPARATOR = "_"


# 缓存模式
class CacheMode:
    # 内存缓存 - 使用内存存储缓存数据
    MEMORY = "MEMORY"
    # Redis缓存 - 使用Redis服务器存储缓存数据
    REDIS = "REDIS"
    # 不使用缓存 - 将使用ttl=0的内存缓存，相当于直接从数据库获取数据
    NONE = "NONE"


SPECIAL_KEY_FORMATS = {
    "LEVEL": "{user_id}" + COMPOSITE_KEY_SEPARATOR + "{group_id}",
    "BAN": "{user_id}" + COMPOSITE_KEY_SEPARATOR + "{group_id}",
    "GROUPS": "{group_id}" + COMPOSITE_KEY_SEPARATOR + "{channel_id}",
}

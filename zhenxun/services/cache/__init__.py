"""
缓存系统模块

提供统一的缓存访问接口，支持内存缓存和Redis缓存

使用示例:
1. 使用Cache类进行缓存操作
```python
from zhenxun.services.cache import Cache
from zhenxun.utils.enum import CacheType

# 创建缓存访问对象
level_cache = Cache[list[LevelUser]](CacheType.LEVEL)

# 获取缓存数据
users = await level_cache.get({"user_id": "123", "group_id": "456"})

# 设置缓存数据
await level_cache.set({"user_id": "123", "group_id": "456"}, users)
```

2. 使用CacheDict作为全局字典
```python
from zhenxun.services.cache.cache_containers import CacheDict

# 创建缓存字典（默认永不过期）
config_dict = CacheDict("global_config")

# 创建有过期时间的缓存字典（1小时后过期）
temp_dict = CacheDict("temp_config", expire=3600)

# 使用字典操作
config_dict["key"] = "value"
value = config_dict["key"]

# 保存缓存数据（可选）
await config_dict.save()
```

3. 使用CacheList作为全局列表
```python
from zhenxun.services.cache.cache_containers import CacheList

# 创建缓存列表（默认永不过期）
message_list = CacheList("recent_messages")

# 创建有过期时间的缓存列表（30分钟后过期）
temp_list = CacheList("temp_messages", expire=1800)

# 使用列表操作
message_list.append("新消息")
message = message_list[0]

# 保存缓存数据（可选）
await message_list.save()
```
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from functools import wraps
from typing import Any, ClassVar, Generic, TypeVar, get_type_hints

from aiocache import Cache as AioCache
from aiocache import SimpleMemoryCache
from aiocache.base import BaseCache
from aiocache.serializers import JsonSerializer
import nonebot
from nonebot.compat import model_dump
from nonebot.utils import is_coroutine_callable
from pydantic import BaseModel

from zhenxun.services.log import logger

from .cache_containers import CacheDict, CacheList
from .config import (
    CACHE_KEY_PREFIX,
    CACHE_KEY_SEPARATOR,
    DEFAULT_EXPIRE,
    LOG_COMMAND,
    SPECIAL_KEY_FORMATS,
    CacheMode,
)

__all__ = [
    "Cache",
    "CacheData",
    "CacheDict",
    "CacheList",
    "CacheManager",
    "CacheRegistry",
    "CacheRoot",
]

T = TypeVar("T")


class Config(BaseModel):
    """缓存配置"""

    cache_mode: str = CacheMode.NONE
    """缓存模式: MEMORY(内存缓存), REDIS(Redis缓存), NONE(不使用缓存)"""
    redis_host: str | None = None
    """redis地址"""
    redis_port: int | None = None
    """redis端口"""
    redis_password: str | None = None
    """redis密码"""
    redis_expire: int = DEFAULT_EXPIRE
    """redis过期时间"""


# 获取配置
driver = nonebot.get_driver()
cache_config = nonebot.get_plugin_config(Config)


class CacheException(Exception):
    """缓存相关异常"""

    def __init__(self, info: str):
        self.info = info

    def __str__(self) -> str:
        return self.info


class CacheModel(BaseModel):
    """缓存数据模型"""

    name: str
    """缓存名称"""
    expire: int = DEFAULT_EXPIRE
    """过期时间（秒）"""
    result_type: type | None = None
    """结果类型"""
    key_format: str | None = None
    """键格式"""

    class Config:
        arbitrary_types_allowed = True


"""
CacheData类是缓存系统的核心组件，它负责管理单个缓存项的数据和生命周期。

设计思路：
1. 每个CacheData实例代表一个具名的缓存项，如"用户列表"、"配置数据"等
2. 它提供了数据的懒加载、自动过期和持久化等功能
3. 可以通过func参数提供一个获取数据的函数，在数据不存在或过期时自动调用
4. 支持直接设置_data属性，方便外部直接操作数据

主要用途：
1. 作为CacheDict和CacheList的后端存储
2. 被CacheManager管理，实现统一的缓存生命周期控制
3. 提供数据过期和自动刷新机制

通常情况下，用户不需要直接使用CacheData，而是通过Cache、CacheDict或CacheList来操作缓存。
"""


class CacheData:
    """缓存数据类"""

    def __init__(
        self,
        name: str,
        func: Callable,
        expire: int = DEFAULT_EXPIRE,
        lazy_load: bool = True,
        cache: BaseCache | AioCache | None = None,
    ):
        """初始化缓存数据

        参数:
            name: 缓存名称
            func: 获取数据的函数
            expire: 过期时间（秒）
            lazy_load: 是否延迟加载
            cache: 缓存后端
        """
        self.name = name.upper()
        self.func = func
        self.expire = expire
        self.lazy_load = lazy_load
        self.cache = cache
        self._data = None
        self._last_update = 0

        # 如果不是延迟加载，立即加载数据
        if not lazy_load:
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                if not loop.is_running():
                    loop.run_until_complete(self.get_data())
            except Exception:
                pass

    async def get_data(self) -> Any:
        """获取数据

        返回:
            Any: 缓存数据
        """
        # 检查是否需要更新
        now = datetime.now().timestamp()
        if self._data is None or (
            self.expire > 0 and now - self._last_update > self.expire
        ):
            # 更新数据
            try:
                self._data = await self.func()
                self._last_update = now
            except Exception as e:
                logger.error(f"获取缓存数据 {self.name} 失败", LOG_COMMAND, e=e)

        return self._data

    async def set_data(self, data: Any) -> bool:
        """设置数据

        参数:
            data: 缓存数据

        返回:
            bool: 是否成功
        """
        try:
            self._data = data
            self._last_update = datetime.now().timestamp()
            # 如果有缓存后端，保存到缓存
            if self.cache and cache_config.cache_mode != CacheMode.NONE:
                await self.cache.set(self.name, data, ttl=self.expire)  # type: ignore
            return True
        except Exception as e:
            logger.error(f"设置缓存数据 {self.name} 失败", LOG_COMMAND, e=e)
            return False

    async def clear(self) -> bool:
        """清除数据

        返回:
            bool: 是否成功
        """
        try:
            self._data = None
            self._last_update = 0
            # 如果有缓存后端，清除缓存
            if self.cache and cache_config.cache_mode != CacheMode.NONE:
                await self.cache.delete(self.name)  # type: ignore
            return True
        except Exception as e:
            logger.error(f"清除缓存数据 {self.name} 失败", LOG_COMMAND, e=e)
            return False


class CacheManager:
    """缓存管理器"""

    _instance: ClassVar["CacheManager | None"] = None
    _cache_backend: BaseCache | AioCache | None = None
    _registry: ClassVar[dict[str, CacheModel]] = {}
    _data: ClassVar[dict[str, CacheData]] = {}
    _list_caches: ClassVar[dict[str, "CacheList"]] = {}
    _dict_caches: ClassVar[dict[str, "CacheDict"]] = {}
    _enabled = False  # 缓存启用标记

    def __new__(cls) -> "CacheManager":
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def enabled(self) -> bool:
        """获取缓存启用状态"""
        return self.__class__._enabled

    @enabled.setter
    def enabled(self, value: bool):
        """设置缓存启用状态"""
        self.__class__._enabled = value

    def enable(self):
        """启用缓存"""
        self.__class__._enabled = True
        logger.info("缓存功能已启用", LOG_COMMAND)

    def disable(self):
        """禁用缓存"""
        self.__class__._enabled = False
        logger.info("缓存功能已禁用", LOG_COMMAND)

    def cache_dict(self, cache_type: str, expire: int = 0) -> CacheDict:
        """获取缓存字典"""
        if cache_type not in self._dict_caches:
            self._dict_caches[cache_type] = CacheDict(cache_type, expire)
        return self._dict_caches[cache_type]

    def cache_list(self, cache_type: str, expire: int = 0) -> CacheList:
        """获取缓存列表"""
        if cache_type not in self._list_caches:
            self._list_caches[cache_type] = CacheList(cache_type, expire)
        return self._list_caches[cache_type]

    def listener(self, cache_type: str):
        """缓存监听器装饰器

        在方法调用后自动刷新缓存数据

        参数:
            cache_type: 缓存类型

        返回:
            Callable: 装饰器
        """

        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(cls, *args, **kwargs):
                # 执行原函数
                result = await func(cls, *args, **kwargs)

                obj = None
                # 如果启用了缓存，自动刷新缓存
                if cache_config.cache_mode != CacheMode.NONE:
                    # 根据返回值类型处理
                    if isinstance(result, tuple) and len(result) > 0:
                        # 处理返回元组的情况，如 update_or_create 返回 (obj, created)
                        obj = result[0]
                else:
                    # 处理返回单个对象的情况
                    obj = result

                # 获取缓存键并刷新缓存
                if (
                    obj
                    and hasattr(cls, "get_cache_key")
                    and hasattr(obj, cls.get_cache_key_field())
                ):
                    key = cls.get_cache_key(obj)
                    if key is not None:
                        await self.invalidate_cache(cache_type, key)

                return result

            return wrapper

        return decorator

    async def get_cache(self, cache_type: str) -> Any:
        """获取指定类型的缓存对象

        此方法返回一个简单的缓存对象，具有 update 方法

        参数:
            cache_type: 缓存类型

        返回:
            Any: 缓存对象
        """

        class CacheAdapter:
            """缓存适配器"""

            def __init__(self, cache_manager: CacheManager, cache_type: str):
                self.cache_manager = cache_manager
                self.cache_type = cache_type

            async def update(self, key: Any, value: Any) -> None:
                """更新缓存

                参数:
                    key: 缓存键
                    value: 缓存值
                """
                # 先清除旧缓存
                await self.cache_manager.invalidate_cache(self.cache_type, key)

                # 如果需要，可以在这里添加重新设置缓存的逻辑
                # 目前我们只清除缓存，让下次查询时自动重建

        return (
            CacheAdapter(self, cache_type)
            if cache_config.cache_mode != CacheMode.NONE
            else None
        )

    @property
    def cache_backend(self) -> BaseCache | AioCache:
        """获取缓存后端"""
        if self._cache_backend is None:
            ttl = cache_config.redis_expire
            if cache_config.cache_mode == CacheMode.NONE:
                ttl = 0
                logger.info("缓存功能已禁用，使用非持久化内存缓存", LOG_COMMAND)
            elif cache_config.cache_mode == CacheMode.REDIS and cache_config.redis_host:
                try:
                    from aiocache import RedisCache

                    # 使用Redis缓存
                    self._cache_backend = RedisCache(
                        serializer=JsonSerializer(),
                        namespace=CACHE_KEY_PREFIX,
                        timeout=30,
                        ttl=cache_config.redis_expire,
                        endpoint=cache_config.redis_host,
                        port=cache_config.redis_port,
                        password=cache_config.redis_password,
                    )
                    logger.info(
                        f"使用Redis缓存，地址: {cache_config.redis_host}",
                        LOG_COMMAND,
                    )
                    return self._cache_backend
                except ImportError as e:
                    logger.error(
                        "导入aiocache[redis]失败，将默认使用内存缓存...",
                        LOG_COMMAND,
                        e=e,
                    )
            else:
                logger.info("使用内存缓存", LOG_COMMAND)
            # 默认使用内存缓存
            self._cache_backend = SimpleMemoryCache(
                serializer=JsonSerializer(),
                namespace=CACHE_KEY_PREFIX,
                timeout=30,
                ttl=ttl,
            )
        return self._cache_backend

    @property
    def _cache(self) -> BaseCache | AioCache:
        """获取缓存后端（别名）"""
        return self.cache_backend

    async def get_cache_data(self, name: str) -> Any:
        """获取缓存数据

        参数:
            name: 缓存名称

        返回:
            Any: 缓存数据
        """
        name = name.upper()
        # 检查是否存在缓存数据
        if name in self._data:
            return await self._data[name].get_data()

        # 尝试从缓存后端获取
        if cache_config.cache_mode != CacheMode.NONE:
            try:
                data = await self.cache_backend.get(name)  # type: ignore
                if data is not None:
                    return data
            except Exception as e:
                logger.error(f"从缓存后端获取数据 {name} 失败", LOG_COMMAND, e=e)
        return None

    async def invalidate_cache(
        self, cache_type: str, key: str | dict[str, Any] | None = None
    ) -> bool:
        """使指定类型的缓存失效

        当数据库中的数据发生变化时，调用此方法清除对应类型的缓存

        参数:
            cache_type: 缓存类型
            key: 缓存键或键参数，为None时清除该类型的所有缓存

        返回:
            bool: 是否成功
        """
        # 如果缓存被禁用或缓存模式为NONE，直接返回True
        if not self.enabled or cache_config.cache_mode == CacheMode.NONE:
            return True

        try:
            if key is not None:
                # 只清除特定的缓存项
                cache_key = self._build_key(cache_type, key)
                await self.cache_backend.delete(cache_key)  # type: ignore
                logger.debug(f"清除缓存: {cache_type}, 键: {key}", LOG_COMMAND)
                return True
            else:
                # 清除指定类型的所有缓存
                logger.debug(f"清除所有 {cache_type} 缓存", LOG_COMMAND)
                return await self.clear(cache_type)
        except Exception as e:
            if f"缓存类型 {cache_type} 不存在" not in str(e):
                logger.warning(f"清除缓存 {cache_type} 失败", LOG_COMMAND, e=e)
            return False

    async def get(
        self, cache_type: str, key: str | dict[str, Any], default: Any = None
    ) -> Any:
        """获取缓存数据

        参数:
            cache_type: 缓存类型
            key: 键或键参数
            default: 默认值

        返回:
            Any: 缓存数据，如果不存在返回默认值
        """
        from zhenxun.services.db_context import DB_TIMEOUT_SECONDS

        # 如果缓存被禁用或缓存模式为NONE，直接返回默认值
        if not self.enabled or cache_config.cache_mode == CacheMode.NONE:
            return default
        cache_key = None
        try:
            cache_key = self._build_key(cache_type, key)
            data = await asyncio.wait_for(
                self.cache_backend.get(cache_key),  # type: ignore
                timeout=DB_TIMEOUT_SECONDS,
            )

            if data is None:
                return default

            # 获取缓存模型
            model = self.get_model(cache_type)

            # 反序列化
            if model.result_type:
                return self._deserialize_value(data, model.result_type)
            return data
        except asyncio.TimeoutError:
            logger.error(f"获取缓存 {cache_type}:{cache_key} 超时", LOG_COMMAND)
            return default
        except Exception as e:
            logger.error(f"获取缓存 {cache_type} 失败", LOG_COMMAND, e=e)
            return default

    async def set(
        self,
        cache_type: str,
        key: str | dict[str, Any],
        value: Any,
        expire: int | None = None,
    ) -> bool:
        """设置缓存数据

        参数:
            cache_type: 缓存类型
            key: 键或键参数
            value: 值
            expire: 过期时间（秒），为None时使用默认值

        返回:
            bool: 是否成功
        """
        from zhenxun.services.db_context import DB_TIMEOUT_SECONDS

        # 如果缓存被禁用或缓存模式为NONE，直接返回False
        if not self.enabled or cache_config.cache_mode == CacheMode.NONE:
            return False
        cache_key = None
        try:
            cache_key = self._build_key(cache_type, key)
            model = self.get_model(cache_type)

            # 序列化
            serialized_value = self._serialize_value(value)

            # 设置过期时间
            ttl = expire if expire is not None else model.expire

            # 设置缓存
            await asyncio.wait_for(
                self.cache_backend.set(cache_key, serialized_value, ttl=ttl),  # type: ignore
                timeout=DB_TIMEOUT_SECONDS,
            )
            return True
        except asyncio.TimeoutError:
            logger.error(f"设置缓存 {cache_type}:{cache_key} 超时", LOG_COMMAND)
            return False
        except Exception as e:
            logger.error(f"设置缓存 {cache_type} 失败", LOG_COMMAND, e=e)
            return False

    async def delete(self, cache_type: str, key: str | dict[str, Any]) -> bool:
        """删除缓存数据

        参数:
            cache_type: 缓存类型
            key: 键或键参数

        返回:
            bool: 是否成功
        """
        # 如果缓存被禁用或缓存模式为NONE，直接返回False
        if not self.enabled or cache_config.cache_mode == CacheMode.NONE:
            return False

        try:
            cache_key = self._build_key(cache_type, key)
            await self.cache_backend.delete(cache_key)  # type: ignore
            return True
        except Exception as e:
            logger.error(f"删除缓存 {cache_type} 失败", LOG_COMMAND, e=e)
            return False

    async def exists(self, cache_type: str, key: str | dict[str, Any]) -> bool:
        """检查缓存是否存在

        参数:
            cache_type: 缓存类型
            key: 键或键参数

        返回:
            bool: 是否存在
        """
        # 如果缓存被禁用或缓存模式为NONE，直接返回False
        if not self.enabled or cache_config.cache_mode == CacheMode.NONE:
            return False

        try:
            cache_key = self._build_key(cache_type, key)
            # 由于aiocache可能没有exists方法，使用get检查
            data = await self.cache_backend.get(cache_key)  # type: ignore
            return data is not None
        except Exception as e:
            logger.error(f"检查缓存 {cache_type} 是否存在失败", LOG_COMMAND, e=e)
            return False

    async def clear(self, cache_type: str | None = None) -> bool:
        """清除缓存

        参数:
            cache_type: 缓存类型，为None时清除所有缓存

        返回:
            bool: 是否成功
        """
        # 如果缓存被禁用或缓存模式为NONE，直接返回False
        if not self.enabled or cache_config.cache_mode == CacheMode.NONE:
            return False

        try:
            if cache_type:
                # 清除指定类型的缓存
                # pattern = f"{cache_type.upper()}{CACHE_KEY_SEPARATOR}*"
                # 由于aiocache可能没有delete_pattern方法，使用其他方式清除
                # 这里简化处理，直接清除所有缓存
                await self.cache_backend.clear()  # type: ignore
            else:
                # 清除所有缓存
                await self.cache_backend.clear()  # type: ignore
            return True
        except Exception as e:
            if f"缓存类型 {cache_type} 不存在" not in str(e):
                logger.warning("清除缓存失败", LOG_COMMAND, e=e)
            return False

    async def close(self):
        """关闭缓存连接"""
        if self._cache_backend:
            try:
                await self._cache_backend.close()  # type: ignore
            except (AttributeError, Exception) as e:
                logger.warning(f"关闭缓存连接失败: {e}", LOG_COMMAND)
            self._cache_backend = None

    def register(
        self,
        name: str,
        result_type: type | None = None,
        expire: int = DEFAULT_EXPIRE,
        key_format: str | None = None,
    ) -> None:
        """注册缓存类型

        参数:
            name: 缓存名称
            result_type: 结果类型
            expire: 过期时间（秒）
            key_format: 键格式
        """
        name = name.upper()
        if name in self._registry:
            logger.warning(f"缓存类型 {name} 已存在，将被覆盖", LOG_COMMAND)

        # 检查是否有特殊键格式
        if not key_format and name in SPECIAL_KEY_FORMATS:
            key_format = SPECIAL_KEY_FORMATS[name]

        self._registry[name] = CacheModel(
            name=name,
            expire=expire,
            result_type=result_type,
            key_format=key_format,
        )
        logger.debug(
            f"注册缓存类型: {name}, 类型: {result_type}, 过期时间: {expire}秒",
            LOG_COMMAND,
        )

    def get_model(self, name: str) -> CacheModel:
        """获取缓存模型

        参数:
            name: 缓存名称

        返回:
            CacheModel: 缓存模型

        异常:
            CacheException: 缓存类型不存在
        """
        name = name.upper()
        if name not in self._registry:
            raise CacheException(f"缓存类型 {name} 不存在")
        return self._registry[name]

    def _build_key(self, cache_type: str, key: str | dict[str, Any]) -> str:
        """构建缓存键

        参数:
            cache_type: 缓存类型
            key: 键或键参数

        返回:
            str: 完整缓存键
        """
        cache_type = cache_type.upper()
        if cache_type not in self._registry:
            raise CacheException(f"缓存类型 {cache_type} 不存在")

        model = self._registry[cache_type]

        # 如果key是字典，使用键格式
        if isinstance(key, dict) and model.key_format:
            try:
                formatted_key = model.key_format.format(**key)
            except KeyError as e:
                raise CacheException(f"键格式错误: {model.key_format}, 缺少参数: {e}")
            return f"{cache_type}{CACHE_KEY_SEPARATOR}{formatted_key}"

        # 否则直接使用key
        return f"{cache_type}{CACHE_KEY_SEPARATOR}{key}"

    def _serialize_value(self, value: Any) -> Any:
        """序列化值

        参数:
            value: 需要序列化的值

        返回:
            Any: 序列化后的值
        """
        if value is None:
            return None

        # 处理datetime
        if isinstance(value, datetime):
            return value.isoformat()

        # 处理Tortoise-ORM Model
        if hasattr(value, "_meta") and hasattr(value, "__dict__"):
            result = {}
            for field in value._meta.fields:
                try:
                    field_value = getattr(value, field)
                    # 跳过反向关系字段
                    if isinstance(field_value, list | set) and hasattr(
                        field_value, "_related_name"
                    ):
                        continue
                    # 跳过外键关系字段
                    if hasattr(field_value, "_meta"):
                        field_value = getattr(
                            field_value, value._meta.fields[field].related_name or "id"
                        )
                    result[field] = self._serialize_value(field_value)
                except AttributeError:
                    continue
            return result

        # 处理Pydantic模型
        elif isinstance(value, BaseModel):
            return model_dump(value)
        elif isinstance(value, dict):
            # 处理字典
            return {str(k): self._serialize_value(v) for k, v in value.items()}
        elif isinstance(value, list | tuple | set):
            # 处理列表、元组、集合
            return [self._serialize_value(item) for item in value]
        elif isinstance(value, int | float | str | bool):
            # 基本类型直接返回
            return value
        else:
            # 其他类型转换为字符串
            return str(value)

    def _deserialize_value(self, value: Any, target_type: type | None = None) -> Any:
        """反序列化值

        参数:
            value: 需要反序列化的值
            target_type: 目标类型

        返回:
            Any: 反序列化后的值
        """
        if value is None:
            return None

        # 如果是字典且指定了目标类型
        if isinstance(value, dict) and target_type:
            # 处理Tortoise-ORM Model
            if hasattr(target_type, "_meta"):
                return self._deserialize_tortoise_model(value, target_type)
            elif hasattr(target_type, "model_validate"):
                return target_type.model_validate(value)
            elif hasattr(target_type, "from_dict"):
                return target_type.from_dict(value)
            elif hasattr(target_type, "parse_obj"):
                return target_type.parse_obj(value)
            else:
                return target_type(**value)

        # 处理列表类型
        if isinstance(value, list):
            if not value:
                return value
            if (
                target_type
                and hasattr(target_type, "__origin__")
                and target_type.__origin__ is list
            ):
                item_type = target_type.__args__[0]
                return [self._deserialize_value(item, item_type) for item in value]
            return [self._deserialize_value(item) for item in value]

        # 处理字典类型
        if isinstance(value, dict):
            return {k: self._deserialize_value(v) for k, v in value.items()}

        return value

    def _deserialize_tortoise_model(self, value: dict, target_type: type) -> Any:
        """反序列化Tortoise-ORM模型

        参数:
            value: 字典数据
            target_type: 目标类型

        返回:
            Any: 反序列化后的模型实例
        """
        # 处理字段值
        processed_value = {}
        for field_name, field_value in value.items():
            if field := target_type._meta.fields_map.get(field_name):
                # 跳过反向关系字段
                if hasattr(field, "_related_name"):
                    continue
                processed_value[field_name] = field_value

        # 创建模型实例
        instance = target_type()
        # 设置字段值
        for field_name, field_value in processed_value.items():
            if field_name in target_type._meta.fields_map:
                field = target_type._meta.fields_map[field_name]
                # 设置字段值
                try:
                    if hasattr(field, "to_python_value"):
                        if not field.field_type:
                            logger.debug(f"字段 {field_name} 类型为空", LOG_COMMAND)
                            continue
                        field_value = field.to_python_value(field_value)
                    setattr(instance, field_name, field_value)
                except Exception as e:
                    logger.warning(f"设置字段 {field_name} 失败", LOG_COMMAND, e=e)

        # 设置 _saved_in_db 标志
        instance._saved_in_db = True
        return instance


# 全局缓存管理器实例
CacheRoot = CacheManager()


class CacheRegistry:
    """缓存注册器"""

    @staticmethod
    def register(
        name: str,
        result_type: type | None = None,
        expire: int = DEFAULT_EXPIRE,
        key_format: str | None = None,
    ):
        """注册缓存类型

        参数:
            name: 缓存名称
            result_type: 结果类型
            expire: 过期时间（秒）
            key_format: 键格式
        """
        CacheRoot.register(name, result_type, expire, key_format)

    @staticmethod
    def invalidate(cache_type: str, key: str | dict[str, Any]):
        """使缓存失效的装饰器

        参数:
            cache_type: 缓存类型
            key: 键或键参数

        返回:
            Callable: 装饰器
        """

        def decorator(func: Callable):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # 执行函数
                result = (
                    await func(*args, **kwargs)
                    if is_coroutine_callable(func)
                    else func(*args, **kwargs)
                )

                # 删除缓存
                if cache_config.cache_mode != CacheMode.NONE:
                    await CacheRoot.delete(cache_type, key)

                return result

            return wrapper

        return decorator


class Cache(Generic[T]):
    """类型化缓存访问接口

    示例:
        ```python
        from zhenxun.services.cache import Cache
        from zhenxun.models.level_user import LevelUser
        from zhenxun.utils.enum import CacheType

        # 创建缓存访问对象
        level_cache = Cache[list[LevelUser]](CacheType.LEVEL)

        # 获取缓存数据
        users = await level_cache.get({"user_id": "123", "group_id": "456"})

        # 设置缓存数据
        await level_cache.set({"user_id": "123", "group_id": "456"}, users)
        ```
    """

    def __init__(self, cache_type: str):
        """初始化缓存访问对象

        参数:
            cache_type: 缓存类型
        """
        self.cache_type = cache_type.upper()

        # 尝试从类型注解获取结果类型
        try:
            type_hints = get_type_hints(self.__class__)
            if "T" in type_hints:
                result_type = type_hints["T"]
                # 确保缓存类型已注册
                try:
                    CacheRoot.get_model(self.cache_type)
                except CacheException:
                    CacheRoot.register(self.cache_type, result_type)
        except Exception:
            pass

    async def get(
        self, key: str | dict[str, Any], default: T | None = None
    ) -> T | None:
        """获取缓存数据

        参数:
            key: 键或键参数
            default: 默认值

        返回:
            T | None: 缓存数据，如果不存在返回默认值
        """
        return await CacheRoot.get(self.cache_type, key, default)

    async def set(
        self, key: str | dict[str, Any], value: T, expire: int | None = None
    ) -> bool:
        """设置缓存数据

        参数:
            key: 键或键参数
            value: 值
            expire: 过期时间（秒），为None时使用默认值

        返回:
            bool: 是否成功
        """
        return await CacheRoot.set(self.cache_type, key, value, expire)

    async def delete(self, key: str | dict[str, Any]) -> bool:
        """删除缓存数据

        参数:
            key: 键或键参数

        返回:
            bool: 是否成功
        """
        return await CacheRoot.delete(self.cache_type, key)

    async def exists(self, key: str | dict[str, Any]) -> bool:
        """检查缓存是否存在

        参数:
            key: 键或键参数

        返回:
            bool: 是否存在
        """
        return await CacheRoot.exists(self.cache_type, key)

    async def clear(self) -> bool:
        """清除此类型的所有缓存

        返回:
            bool: 是否成功
        """
        return await CacheRoot.clear(self.cache_type)


@driver.on_startup
async def _():
    CacheRoot.enabled = True
    logger.info("缓存系统已启用", LOG_COMMAND)


@driver.on_shutdown
async def _():
    await CacheRoot.close()

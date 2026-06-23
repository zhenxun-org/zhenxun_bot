from dataclasses import dataclass
import time
from typing import Any, Generic, TypeVar
import weakref

T = TypeVar("T")

DEFAULT_CACHE_MAX_ITEMS = 10000


@dataclass
class CacheData(Generic[T]):
    """缓存数据类，存储数据和过期时间"""

    value: T
    expire_time: float = 0  # 0表示永不过期


class CacheDict(Generic[T]):
    """缓存字典类，提供类似普通字典的接口，数据只存储在内存中"""

    _instances: weakref.WeakSet = weakref.WeakSet()

    def __init__(self, name: str, expire: int = 0, max_items: int | None = None):
        """初始化缓存字典

        参数:
            name: 字典名称
            expire: 过期时间（秒），默认为0表示永不过期
            max_items: 最大缓存项数，None 使用统一默认值，0 表示不限制
        """
        self.name = name.upper()
        self.expire = expire
        self.max_items = DEFAULT_CACHE_MAX_ITEMS if max_items is None else max_items
        self._data: dict[str, CacheData[T]] = {}
        self.__class__._instances.add(self)

    def expire_time(self, key: str) -> float:
        """获取字典项的过期时间"""
        data = self._data.get(key)
        if data is None:
            return 0
        if data.expire_time > 0 and data.expire_time < time.time():
            del self._data[key]
            return 0
        return data.expire_time

    def __getitem__(self, key: str) -> T:
        """获取字典项

        参数:
            key: 字典键

        返回:
            T: 字典值
        """
        if value := self._data.get(key):
            if value.expire_time > 0 and value.expire_time < time.time():
                del self._data[key]
                raise KeyError(f"键 {key} 已过期")
            return value.value
        raise KeyError(f"键 {key} 不存在")

    def __setitem__(self, key: str, value: T) -> None:
        """设置字典项

        参数:
            key: 字典键
            value: 字典值
        """
        expire_time = time.time() + self.expire if self.expire > 0 else 0
        self._data[key] = CacheData(value=value, expire_time=expire_time)
        self._enforce_limit()

    def __delitem__(self, key: str) -> None:
        """删除字典项

        参数:
            key: 字典键
        """
        if key in self._data:
            del self._data[key]

    def __contains__(self, key: str) -> bool:
        """检查键是否存在

        参数:
            key: 字典键

        返回:
            bool: 是否存在
        """
        data = self._data.get(key)
        if data is None:
            return False
        if data.expire_time > 0 and data.expire_time < time.time():
            del self._data[key]
            return False
        return True

    def get(self, key: str, default: Any = None) -> T | None:
        """获取字典项，如果不存在返回默认值

        参数:
            key: 字典键
            default: 默认值

        返回:
            Any: 字典值或默认值
        """
        if value := self._data.get(key):
            if value.expire_time > 0 and value.expire_time < time.time():
                del self._data[key]
                return default
            return default if value.value is None else value.value
        return default

    def set(self, key: str, value: Any, expire: int | None = None):
        """设置字典项

        参数:
            key: 字典键
            value: 字典值
            expire: 过期时间（秒），为None时使用默认值
        """
        # 计算过期时间
        expire_time = 0
        if expire is not None and expire > 0:
            expire_time = time.time() + expire
        elif self.expire > 0:
            expire_time = time.time() + self.expire

        self._data[key] = CacheData(value=value, expire_time=expire_time)
        self._enforce_limit()

    def pop(self, key: str, default: Any = None) -> T:
        """删除并返回字典项

        参数:
            key: 字典键
            default: 默认值

        返回:
            Any: 字典值或默认值
        """
        data = self._data.get(key)
        if data is None:
            return default
        if data.expire_time > 0 and data.expire_time < time.time():
            del self._data[key]
            return default
        del self._data[key]
        return data.value

    def clear(self) -> None:
        """清空字典"""
        self._data.clear()

    def stats(self) -> dict[str, int]:
        """返回当前缓存条目统计。"""
        self._clean_expired()
        return {"items": len(self._data), "max_items": self.max_items}

    @classmethod
    def stats_all(cls) -> dict[str, dict[str, int]]:
        """返回所有 CacheDict 实例的条目统计。"""
        result: dict[str, dict[str, int]] = {}
        for cache in list(cls._instances):
            stats = cache.stats()
            if stats["items"]:
                result[cache.name] = stats
        return result

    @classmethod
    def clear_all(cls) -> dict[str, int]:
        """清空所有 CacheDict，返回各缓存清理的条目数。"""
        result: dict[str, int] = {}
        for cache in list(cls._instances):
            size = len(cache._data)
            if size:
                cache.clear()
                result[cache.name] = result.get(cache.name, 0) + size
        return result

    def keys(self) -> list[str]:
        """获取所有键

        返回:
            list[str]: 键列表
        """
        # 清理过期的键
        self._clean_expired()
        return list(self._data.keys())

    def values(self) -> list[Any]:
        """获取所有值

        返回:
            list[Any]: 值列表
        """
        # 清理过期的键
        self._clean_expired()
        return [data.value for data in self._data.values()]

    def items(self) -> list[tuple[str, T]]:
        """获取所有键值对

        返回:
            list[tuple[str, Any]]: 键值对列表
        """
        # 清理过期的键
        self._clean_expired()
        return [(key, data.value) for key, data in self._data.items()]

    def _clean_expired(self):
        """清理过期的键"""
        now = time.time()
        expired_keys = [
            key
            for key, data in self._data.items()
            if data.expire_time > 0 and data.expire_time < now
        ]
        for key in expired_keys:
            del self._data[key]

    def _enforce_limit(self) -> None:
        if self.max_items <= 0:
            return
        while len(self._data) > self.max_items:
            self._data.pop(next(iter(self._data)))

    def __len__(self) -> int:
        """获取字典长度

        返回:
            int: 字典长度
        """
        # 清理过期的键
        self._clean_expired()
        return len(self._data)

    def __str__(self) -> str:
        """字符串表示

        返回:
            str: 字符串表示
        """
        # 清理过期的键
        self._clean_expired()
        return f"CacheDict({self.name}, {len(self._data)} items)"

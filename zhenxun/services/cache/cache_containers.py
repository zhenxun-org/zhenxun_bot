from dataclasses import dataclass
import time
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheData(Generic[T]):
    """缓存数据类，存储数据和过期时间"""

    value: T
    expire_time: float = 0  # 0表示永不过期


class CacheDict:
    """缓存字典类，提供类似普通字典的接口，数据只存储在内存中"""

    def __init__(self, name: str, expire: int = 0):
        """初始化缓存字典

        参数:
            name: 字典名称
            expire: 过期时间（秒），默认为0表示永不过期
        """
        self.name = name.upper()
        self.expire = expire
        self._data: dict[str, CacheData[Any]] = {}

    def __getitem__(self, key: str) -> Any:
        """获取字典项

        参数:
            key: 字典键

        返回:
            Any: 字典值
        """
        data = self._data.get(key)
        if data is None:
            return None

        # 检查是否过期
        if data.expire_time > 0 and data.expire_time < time.time():
            del self._data[key]
            return None

        return data.value

    def __setitem__(self, key: str, value: Any) -> None:
        """设置字典项

        参数:
            key: 字典键
            value: 字典值
        """
        expire_time = time.time() + self.expire if self.expire > 0 else 0
        self._data[key] = CacheData(value=value, expire_time=expire_time)

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
        if key not in self._data:
            return False

        # 检查是否过期
        data = self._data[key]
        if data.expire_time > 0 and data.expire_time < time.time():
            del self._data[key]
            return False

        return True

    def get(self, key: str, default: Any = None) -> Any:
        """获取字典项，如果不存在返回默认值

        参数:
            key: 字典键
            default: 默认值

        返回:
            Any: 字典值或默认值
        """
        value = self[key]
        return default if value is None else value

    def set(self, key: str, value: Any, expire: int | None = None) -> None:
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

    def pop(self, key: str, default: Any = None) -> Any:
        """删除并返回字典项

        参数:
            key: 字典键
            default: 默认值

        返回:
            Any: 字典值或默认值
        """
        if key not in self._data:
            return default

        data = self._data.pop(key)

        # 检查是否过期
        if data.expire_time > 0 and data.expire_time < time.time():
            return default

        return data.value

    def clear(self) -> None:
        """清空字典"""
        self._data.clear()

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

    def items(self) -> list[tuple[str, Any]]:
        """获取所有键值对

        返回:
            list[tuple[str, Any]]: 键值对列表
        """
        # 清理过期的键
        self._clean_expired()
        return [(key, data.value) for key, data in self._data.items()]

    def _clean_expired(self) -> None:
        """清理过期的键"""
        now = time.time()
        expired_keys = [
            key
            for key, data in self._data.items()
            if data.expire_time > 0 and data.expire_time < now
        ]
        for key in expired_keys:
            del self._data[key]

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


class CacheList:
    """缓存列表类，提供类似普通列表的接口，数据只存储在内存中"""

    def __init__(self, name: str, expire: int = 0):
        """初始化缓存列表

        参数:
            name: 列表名称
            expire: 过期时间（秒），默认为0表示永不过期
        """
        self.name = name.upper()
        self.expire = expire
        self._data: list[CacheData[Any]] = []
        self._expire_time = 0

        # 如果设置了过期时间，计算整个列表的过期时间
        if self.expire > 0:
            self._expire_time = time.time() + self.expire

    def __getitem__(self, index: int) -> Any:
        """获取列表项

        参数:
            index: 列表索引

        返回:
            Any: 列表值
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()
            raise IndexError(f"列表索引 {index} 超出范围")

        if 0 <= index < len(self._data):
            return self._data[index].value
        raise IndexError(f"列表索引 {index} 超出范围")

    def __setitem__(self, index: int, value: Any) -> None:
        """设置列表项

        参数:
            index: 列表索引
            value: 列表值
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()

        # 确保索引有效
        while len(self._data) <= index:
            self._data.append(CacheData(value=None))
        self._data[index] = CacheData(value=value)

        # 更新过期时间
        self._update_expire_time()

    def __delitem__(self, index: int) -> None:
        """删除列表项

        参数:
            index: 列表索引
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()
            raise IndexError(f"列表索引 {index} 超出范围")

        if not 0 <= index < len(self._data):
            raise IndexError(f"列表索引 {index} 超出范围")
        del self._data[index]
        # 更新过期时间
        self._update_expire_time()

    def __len__(self) -> int:
        """获取列表长度

        返回:
            int: 列表长度
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()
        return len(self._data)

    def append(self, value: Any) -> None:
        """添加列表项

        参数:
            value: 列表值
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()

        self._data.append(CacheData(value=value))

        # 更新过期时间
        self._update_expire_time()

    def extend(self, values: list[Any]) -> None:
        """扩展列表

        参数:
            values: 要添加的值列表
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()

        self._data.extend([CacheData(value=v) for v in values])

        # 更新过期时间
        self._update_expire_time()

    def insert(self, index: int, value: Any) -> None:
        """插入列表项

        参数:
            index: 插入位置
            value: 列表值
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()

        self._data.insert(index, CacheData(value=value))

        # 更新过期时间
        self._update_expire_time()

    def pop(self, index: int = -1) -> Any:
        """删除并返回列表项

        参数:
            index: 列表索引，默认为最后一项

        返回:
            Any: 列表值
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()
            raise IndexError("从空列表中弹出")

        if not self._data:
            raise IndexError("从空列表中弹出")

        item = self._data.pop(index)

        # 更新过期时间
        self._update_expire_time()

        return item.value

    def remove(self, value: Any) -> None:
        """删除第一个匹配的列表项

        参数:
            value: 要删除的值
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()
            raise ValueError(f"{value} 不在列表中")

        # 查找匹配的项
        for i, item in enumerate(self._data):
            if item.value == value:
                del self._data[i]
                # 更新过期时间
                self._update_expire_time()
                return

        raise ValueError(f"{value} 不在列表中")

    def clear(self) -> None:
        """清空列表"""
        self._data.clear()
        # 重置过期时间
        self._update_expire_time()

    def index(self, value: Any, start: int = 0, end: int | None = None) -> int:
        """查找值的索引

        参数:
            value: 要查找的值
            start: 起始索引
            end: 结束索引

        返回:
            int: 索引位置
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()
            raise ValueError(f"{value} 不在列表中")

        end = end if end is not None else len(self._data)

        for i in range(start, min(end, len(self._data))):
            if self._data[i].value == value:
                return i

        raise ValueError(f"{value} 不在列表中")

    def count(self, value: Any) -> int:
        """计算值出现的次数

        参数:
            value: 要计数的值

        返回:
            int: 出现次数
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()
            return 0

        # sourcery skip: simplify-constant-sum
        return sum(1 for item in self._data if item.value == value)

    def _is_expired(self) -> bool:
        """检查整个列表是否过期"""
        return self._expire_time > 0 and self._expire_time < time.time()

    def _update_expire_time(self) -> None:
        """更新过期时间"""
        self._expire_time = time.time() + self.expire if self.expire > 0 else 0

    def __str__(self) -> str:
        """字符串表示

        返回:
            str: 字符串表示
        """
        # 检查整个列表是否过期
        if self._is_expired():
            self.clear()
        return f"CacheList({self.name}, {len(self._data)} items)"

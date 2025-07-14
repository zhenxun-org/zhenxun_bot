import asyncio
from collections.abc import Iterable
import contextlib
from typing import Any, ClassVar
from typing_extensions import Self

from tortoise.backends.base.client import BaseDBAsyncClient
from tortoise.exceptions import IntegrityError, MultipleObjectsReturned
from tortoise.models import Model as TortoiseModel
from tortoise.transactions import in_transaction

from zhenxun.services.cache import CacheRoot
from zhenxun.services.log import logger
from zhenxun.utils.enum import DbLockType

from .config import LOG_COMMAND, db_model
from .utils import with_db_timeout


class Model(TortoiseModel):
    """
    增强的ORM基类，解决锁嵌套问题
    """

    sem_data: ClassVar[dict[str, dict[str, asyncio.Semaphore]]] = {}
    _current_locks: ClassVar[dict[int, DbLockType]] = {}  # 跟踪当前协程持有的锁

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__module__ not in db_model.models:
            db_model.models.append(cls.__module__)

        if func := getattr(cls, "_run_script", None):
            db_model.script_method.append((cls.__module__, func))

    @classmethod
    def get_cache_type(cls) -> str | None:
        """获取缓存类型"""
        return getattr(cls, "cache_type", None)

    @classmethod
    def get_cache_key_field(cls) -> str | tuple[str]:
        """获取缓存键字段"""
        return getattr(cls, "cache_key_field", "id")

    @classmethod
    def get_cache_key(cls, instance) -> str | None:
        """获取缓存键

        参数:
            instance: 模型实例

        返回:
            str | None: 缓存键，如果无法获取则返回None
        """
        from zhenxun.services.cache.config import COMPOSITE_KEY_SEPARATOR

        key_field = cls.get_cache_key_field()

        if isinstance(key_field, tuple):
            # 多字段主键
            key_parts = []
            for field in key_field:
                if hasattr(instance, field):
                    value = getattr(instance, field, None)
                    key_parts.append(value if value is not None else "")
                else:
                    # 如果缺少任何必要的字段，返回None
                    key_parts.append("")

            # 如果没有有效参数，返回None
            return COMPOSITE_KEY_SEPARATOR.join(key_parts) if key_parts else None
        elif hasattr(instance, key_field):
            value = getattr(instance, key_field, None)
            return str(value) if value is not None else None

        return None

    @classmethod
    def get_semaphore(cls, lock_type: DbLockType):
        enable_lock = getattr(cls, "enable_lock", None)
        if not enable_lock or lock_type not in enable_lock:
            return None

        if cls.__name__ not in cls.sem_data:
            cls.sem_data[cls.__name__] = {}
        if lock_type not in cls.sem_data[cls.__name__]:
            cls.sem_data[cls.__name__][lock_type] = asyncio.Semaphore(1)
        return cls.sem_data[cls.__name__][lock_type]

    @classmethod
    def _require_lock(cls, lock_type: DbLockType) -> bool:
        """检查是否需要真正加锁"""
        task_id = id(asyncio.current_task())
        return cls._current_locks.get(task_id) != lock_type

    @classmethod
    @contextlib.asynccontextmanager
    async def _lock_context(cls, lock_type: DbLockType):
        """带重入检查的锁上下文"""
        task_id = id(asyncio.current_task())
        need_lock = cls._require_lock(lock_type)

        if need_lock and (sem := cls.get_semaphore(lock_type)):
            cls._current_locks[task_id] = lock_type
            async with sem:
                yield
            cls._current_locks.pop(task_id, None)
        else:
            yield

    @classmethod
    async def create(
        cls, using_db: BaseDBAsyncClient | None = None, **kwargs: Any
    ) -> Self:
        """创建数据（使用CREATE锁）"""
        async with cls._lock_context(DbLockType.CREATE):
            # 直接调用父类的_create方法避免触发save的锁
            result = await super().create(using_db=using_db, **kwargs)
            if cache_type := cls.get_cache_type():
                await CacheRoot.invalidate_cache(cache_type, cls.get_cache_key(result))
            return result

    @classmethod
    async def get_or_create(
        cls,
        defaults: dict | None = None,
        using_db: BaseDBAsyncClient | None = None,
        **kwargs: Any,
    ) -> tuple[Self, bool]:
        """获取或创建数据（无锁版本，依赖数据库约束）"""
        result = await super().get_or_create(
            defaults=defaults, using_db=using_db, **kwargs
        )
        if cache_type := cls.get_cache_type():
            await CacheRoot.invalidate_cache(cache_type, cls.get_cache_key(result[0]))
        return result

    @classmethod
    async def update_or_create(
        cls,
        defaults: dict | None = None,
        using_db: BaseDBAsyncClient | None = None,
        **kwargs: Any,
    ) -> tuple[Self, bool]:
        """更新或创建数据（使用UPSERT锁）"""
        async with cls._lock_context(DbLockType.UPSERT):
            try:
                # 先尝试更新（带行锁）
                async with in_transaction():
                    if obj := await cls.filter(**kwargs).select_for_update().first():
                        await obj.update_from_dict(defaults or {})
                        await obj.save()
                        result = (obj, False)
                    else:
                        # 创建时不重复加锁
                        result = await cls.create(**kwargs, **(defaults or {})), True

                if cache_type := cls.get_cache_type():
                    await CacheRoot.invalidate_cache(
                        cache_type, cls.get_cache_key(result[0])
                    )
                return result
            except IntegrityError:
                # 处理极端情况下的唯一约束冲突
                obj = await cls.get(**kwargs)
                return obj, False

    async def save(
        self,
        using_db: BaseDBAsyncClient | None = None,
        update_fields: Iterable[str] | None = None,
        force_create: bool = False,
        force_update: bool = False,
    ):
        """保存数据（根据操作类型自动选择锁）"""
        lock_type = (
            DbLockType.CREATE
            if getattr(self, "id", None) is None
            else DbLockType.UPDATE
        )
        async with self._lock_context(lock_type):
            await super().save(
                using_db=using_db,
                update_fields=update_fields,
                force_create=force_create,
                force_update=force_update,
            )
            if cache_type := getattr(self, "cache_type", None):
                await CacheRoot.invalidate_cache(
                    cache_type, self.__class__.get_cache_key(self)
                )

    async def delete(self, using_db: BaseDBAsyncClient | None = None):
        cache_type = getattr(self, "cache_type", None)
        key = self.__class__.get_cache_key(self) if cache_type else None
        # 执行删除操作
        await super().delete(using_db=using_db)

        # 清除缓存
        if cache_type:
            await CacheRoot.invalidate_cache(cache_type, key)

    @classmethod
    async def safe_get_or_none(
        cls,
        *args,
        using_db: BaseDBAsyncClient | None = None,
        clean_duplicates: bool = True,
        **kwargs: Any,
    ) -> Self | None:
        """安全地获取一条记录或None，处理存在多个记录时返回最新的那个
        注意，默认会删除重复的记录，仅保留最新的

        参数:
            *args: 查询参数
            using_db: 数据库连接
            clean_duplicates: 是否删除重复的记录，仅保留最新的
            **kwargs: 查询参数

        返回:
            Self | None: 查询结果，如果不存在返回None
        """
        try:
            # 先尝试使用 get_or_none 获取单个记录
            try:
                return await with_db_timeout(
                    cls.get_or_none(*args, using_db=using_db, **kwargs),
                    operation=f"{cls.__name__}.get_or_none",
                )
            except MultipleObjectsReturned:
                # 如果出现多个记录的情况，进行特殊处理
                logger.warning(
                    f"{cls.__name__} safe_get_or_none 发现多个记录: {kwargs}",
                    LOG_COMMAND,
                )

                # 查询所有匹配记录
                records = await with_db_timeout(
                    cls.filter(*args, **kwargs).all(),
                    operation=f"{cls.__name__}.filter.all",
                )

                if not records:
                    return None

                # 如果需要清理重复记录
                if clean_duplicates and hasattr(records[0], "id"):
                    # 按 id 排序
                    records = sorted(
                        records, key=lambda x: getattr(x, "id", 0), reverse=True
                    )
                    for record in records[1:]:
                        try:
                            await with_db_timeout(
                                record.delete(),
                                operation=f"{cls.__name__}.delete_duplicate",
                            )
                            logger.info(
                                f"{cls.__name__} 删除重复记录:"
                                f" id={getattr(record, 'id', None)}",
                                LOG_COMMAND,
                            )
                        except Exception as del_e:
                            logger.error(f"删除重复记录失败: {del_e}")
                    return records[0]
                # 如果不需要清理或没有 id 字段，则返回最新的记录
                if hasattr(cls, "id"):
                    return await with_db_timeout(
                        cls.filter(*args, **kwargs).order_by("-id").first(),
                        operation=f"{cls.__name__}.filter.order_by.first",
                    )
                # 如果没有 id 字段，则返回第一个记录
                return await with_db_timeout(
                    cls.filter(*args, **kwargs).first(),
                    operation=f"{cls.__name__}.filter.first",
                )
        except asyncio.TimeoutError:
            logger.error(
                f"数据库操作超时: {cls.__name__}.safe_get_or_none", LOG_COMMAND
            )
            return None
        except Exception as e:
            # 其他类型的错误则继续抛出
            logger.error(
                f"数据库操作异常: {cls.__name__}.safe_get_or_none, {e!s}", LOG_COMMAND
            )
            raise

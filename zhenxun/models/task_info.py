from typing import ClassVar

from tortoise import fields

from zhenxun.services.cache.runtime_cache import TaskInfoMemoryCache, TaskInfoSnapshot
from zhenxun.services.db_context import Model


class TaskInfo(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    module = fields.CharField(255, description="被动技能模块名")
    """被动技能模块名"""
    name = fields.CharField(255, description="被动技能名称")
    """被动技能名称"""
    status = fields.BooleanField(default=True, description="全局开关状态")
    """全局开关状态"""
    load_status = fields.BooleanField(default=True, description="进群默认开关状态")
    """加载状态"""
    default_status = fields.BooleanField(default=True, description="进群默认开关状态")
    """全局开关状态"""
    run_time = fields.CharField(255, null=True, description="运行时间")
    """运行时间"""
    run_count = fields.IntField(default=0, description="运行次数")
    """运行次数"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "task_info"
        table_description = "被动技能基本信息"
        indexes: ClassVar = [("module",)]

    @classmethod
    async def create(cls, *args, **kwargs):
        result = await super().create(*args, **kwargs)
        await TaskInfoMemoryCache.upsert_from_model(result)
        return result

    @classmethod
    async def update_or_create(cls, *args, **kwargs):
        result = await super().update_or_create(*args, **kwargs)
        await TaskInfoMemoryCache.upsert_from_model(result[0])
        return result

    async def save(self, *args, **kwargs):
        await super().save(*args, **kwargs)
        await TaskInfoMemoryCache.upsert_from_model(self)

    async def delete(self, *args, **kwargs):
        module = self.module
        await super().delete(*args, **kwargs)
        await TaskInfoMemoryCache.remove(module)

    @classmethod
    async def get_task(
        cls, *, module: str | None = None, name: str | None = None
    ) -> TaskInfoSnapshot | None:
        if module:
            return await TaskInfoMemoryCache.get(module)
        if name:
            return await TaskInfoMemoryCache.get_by_name(name)
        return None

    @classmethod
    async def get_tasks(
        cls,
        *,
        status: bool | None = None,
        load_status: bool | None = None,
        default_status: bool | None = None,
        modules: list[str] | None = None,
    ) -> list[TaskInfoSnapshot]:
        tasks = await TaskInfoMemoryCache.get_all()
        module_set = set(modules) if modules else None
        result: list[TaskInfoSnapshot] = []
        for task in tasks:
            if status is not None and task.status != status:
                continue
            if load_status is not None and task.load_status != load_status:
                continue
            if default_status is not None and task.default_status != default_status:
                continue
            if module_set is not None and task.module not in module_set:
                continue
            result.append(task)
        return result

    @classmethod
    async def get_modules(
        cls,
        *,
        status: bool | None = None,
        load_status: bool | None = None,
        default_status: bool | None = None,
    ) -> list[str]:
        tasks = await cls.get_tasks(
            status=status,
            load_status=load_status,
            default_status=default_status,
        )
        return [task.module for task in tasks]

    @classmethod
    async def _run_script(cls):
        return []

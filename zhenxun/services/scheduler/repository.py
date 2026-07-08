"""
数据持久层 (Repository)

封装所有对 ScheduledJob 模型的数据库操作，将数据访问逻辑与业务逻辑分离。
"""

from datetime import datetime
from typing import Any

from tortoise.queryset import QuerySet

from zhenxun.models.scheduled_job import ScheduledJob


class ScheduleRepository:
    """封装 ScheduledJob 模型的数据库操作"""

    @staticmethod
    async def get_by_id(schedule_id: int) -> ScheduledJob | None:
        """
        通过ID获取任务

        参数:
            schedule_id: 任务ID。

        返回:
            ScheduledJob | None: 任务对象，不存在时返回None。
        """
        return await ScheduledJob.get_or_none(id=schedule_id)

    @staticmethod
    async def get_all_enabled() -> list[ScheduledJob]:
        """
        获取所有启用的任务

        返回:
            list[ScheduledJob]: 所有启用状态的任务列表。
        """
        return await ScheduledJob.filter(is_enabled=True).all()

    @staticmethod
    async def get_all(plugin_name: str | None = None) -> list[ScheduledJob]:
        """获取所有任务，可按插件名过滤"""
        if plugin_name:
            return await ScheduledJob.filter(plugin_name=plugin_name).all()
        return await ScheduledJob.all()

    @staticmethod
    async def update_run_status(schedule: ScheduledJob, is_success: bool):
        """
        统一更新任务的最后运行状态
        """
        schedule.last_run_at = datetime.now()
        if is_success:
            schedule.last_run_status = "SUCCESS"
            schedule.consecutive_failures = 0
        else:
            schedule.last_run_status = "FAILURE"
            schedule.consecutive_failures = (schedule.consecutive_failures or 0) + 1

        await schedule.save(
            update_fields=["last_run_at", "last_run_status", "consecutive_failures"]
        )

    @staticmethod
    async def save(schedule: ScheduledJob, update_fields: list[str] | None = None):
        """
        保存任务

        参数:
            schedule: 要保存的任务对象。
            update_fields: 要更新的字段列表，None表示更新所有字段。
        """
        await schedule.save(update_fields=update_fields)

    @staticmethod
    async def exists(**kwargs: Any) -> bool:
        """检查任务是否存在"""
        return await ScheduledJob.exists(**kwargs)

    @staticmethod
    async def get_by_plugin_and_group(
        plugin_name: str, group_ids: list[str]
    ) -> list[ScheduledJob]:
        """[DEPRECATED] 根据插件和群组ID列表获取任务"""
        return await ScheduledJob.filter(
            plugin_name=plugin_name, target_descriptor__in=group_ids
        ).all()

    @staticmethod
    async def update_or_create(
        defaults: dict, **kwargs: Any
    ) -> tuple[ScheduledJob, bool]:
        """更新或创建任务"""
        return await ScheduledJob.update_or_create(defaults=defaults, **kwargs)

    @staticmethod
    async def query_schedules(
        page: int | None = None, page_size: int | None = None, **filters: Any
    ) -> tuple[list[ScheduledJob], int]:
        """
        根据任意条件查询任务列表

        参数:
            page: 页码（从1开始）
            page_size: 每页数量
            **filters: 过滤条件，如 group_id="123", plugin_name="abc"

        返回:
            tuple[list[ScheduledJob], int]: (任务列表, 总数)
        """
        cleaned_filters = {k: v for k, v in filters.items() if v is not None}
        query = ScheduledJob.filter(**cleaned_filters)

        total_count = await query.count()

        if page is not None and page_size is not None:
            offset = (page - 1) * page_size
            query = query.offset(offset).limit(page_size)

        return await query.all(), total_count

    @staticmethod
    def filter(**kwargs: Any) -> QuerySet[ScheduledJob]:
        """提供一个通用的过滤查询接口，供Targeter使用"""
        return ScheduledJob.filter(**kwargs)

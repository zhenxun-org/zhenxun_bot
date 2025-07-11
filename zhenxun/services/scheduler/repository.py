"""
数据持久层 (Repository)

封装所有对 ScheduleInfo 模型的数据库操作，将数据访问逻辑与业务逻辑分离。
"""

from typing import Any

from tortoise.queryset import QuerySet

from zhenxun.models.schedule_info import ScheduleInfo


class ScheduleRepository:
    """封装 ScheduleInfo 模型的数据库操作"""

    @staticmethod
    async def get_by_id(schedule_id: int) -> ScheduleInfo | None:
        """通过ID获取任务"""
        return await ScheduleInfo.get_or_none(id=schedule_id)

    @staticmethod
    async def get_all_enabled() -> list[ScheduleInfo]:
        """获取所有启用的任务"""
        return await ScheduleInfo.filter(is_enabled=True).all()

    @staticmethod
    async def get_all(plugin_name: str | None = None) -> list[ScheduleInfo]:
        """获取所有任务，可按插件名过滤"""
        if plugin_name:
            return await ScheduleInfo.filter(plugin_name=plugin_name).all()
        return await ScheduleInfo.all()

    @staticmethod
    async def save(schedule: ScheduleInfo, update_fields: list[str] | None = None):
        """保存任务"""
        await schedule.save(update_fields=update_fields)

    @staticmethod
    async def exists(**kwargs: Any) -> bool:
        """检查任务是否存在"""
        return await ScheduleInfo.exists(**kwargs)

    @staticmethod
    async def get_by_plugin_and_group(
        plugin_name: str, group_ids: list[str]
    ) -> list[ScheduleInfo]:
        """根据插件和群组ID列表获取任务"""
        return await ScheduleInfo.filter(
            plugin_name=plugin_name, group_id__in=group_ids
        ).all()

    @staticmethod
    async def update_or_create(
        defaults: dict, **kwargs: Any
    ) -> tuple[ScheduleInfo, bool]:
        """更新或创建任务"""
        return await ScheduleInfo.update_or_create(defaults=defaults, **kwargs)

    @staticmethod
    async def query_schedules(**filters: Any) -> list[ScheduleInfo]:
        """
        根据任意条件查询任务列表

        参数:
            **filters: 过滤条件，如 group_id="123", plugin_name="abc"

        返回:
            list[ScheduleInfo]: 任务列表
        """
        cleaned_filters = {k: v for k, v in filters.items() if v is not None}
        if not cleaned_filters:
            return await ScheduleInfo.all()
        return await ScheduleInfo.filter(**cleaned_filters).all()

    @staticmethod
    def filter(**kwargs: Any) -> QuerySet[ScheduleInfo]:
        """提供一个通用的过滤查询接口，供Targeter使用"""
        return ScheduleInfo.filter(**kwargs)

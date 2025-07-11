"""
目标选择器 (Targeter)

提供链式API，用于构建和执行对多个定时任务的批量操作。
"""

from collections.abc import Callable, Coroutine
from typing import Any

from .adapter import APSchedulerAdapter
from .repository import ScheduleRepository


class ScheduleTargeter:
    """
    一个用于构建和执行定时任务批量操作的目标选择器。
    """

    def __init__(self, manager: Any, **filters: Any):
        """初始化目标选择器"""
        self._manager = manager
        self._filters = {k: v for k, v in filters.items() if v is not None}

    async def _get_schedules(self):
        """根据过滤器获取任务"""
        query = ScheduleRepository.filter(**self._filters)
        return await query.all()

    def _generate_target_description(self) -> str:
        """根据过滤条件生成友好的目标描述"""
        if "id" in self._filters:
            return f"任务 ID {self._filters['id']} 的"

        parts = []
        if "group_id" in self._filters:
            group_id = self._filters["group_id"]
            if group_id == self._manager.ALL_GROUPS:
                parts.append("所有群组中")
            else:
                parts.append(f"群 {group_id} 中")

        if "plugin_name" in self._filters:
            parts.append(f"插件 '{self._filters['plugin_name']}' 的")

        if not parts:
            return "所有"

        return "".join(parts)

    async def _apply_operation(
        self,
        operation_func: Callable[[int], Coroutine[Any, Any, tuple[bool, str]]],
        operation_name: str,
    ) -> tuple[int, str]:
        """通用的操作应用模板"""
        schedules = await self._get_schedules()
        if not schedules:
            target_desc = self._generate_target_description()
            return 0, f"没有找到{target_desc}可供{operation_name}的任务。"

        success_count = 0
        for schedule in schedules:
            success, _ = await operation_func(schedule.id)
            if success:
                success_count += 1

        target_desc = self._generate_target_description()
        return (
            success_count,
            f"成功{operation_name}了{target_desc} {success_count} 个任务。",
        )

    async def pause(self) -> tuple[int, str]:
        """
        暂停匹配的定时任务

        返回:
            tuple[int, str]: (成功暂停的任务数量, 操作结果消息)。
        """
        return await self._apply_operation(self._manager.pause_schedule, "暂停")

    async def resume(self) -> tuple[int, str]:
        """
        恢复匹配的定时任务

        返回:
            tuple[int, str]: (成功恢复的任务数量, 操作结果消息)。
        """
        return await self._apply_operation(self._manager.resume_schedule, "恢复")

    async def remove(self) -> tuple[int, str]:
        """
        移除匹配的定时任务

        返回:
            tuple[int, str]: (成功移除的任务数量, 操作结果消息)。
        """
        schedules = await self._get_schedules()
        if not schedules:
            target_desc = self._generate_target_description()
            return 0, f"没有找到{target_desc}可供移除的任务。"

        for schedule in schedules:
            APSchedulerAdapter.remove_job(schedule.id)

        query = ScheduleRepository.filter(**self._filters)
        count = await query.delete()
        target_desc = self._generate_target_description()
        return count, f"成功移除了{target_desc} {count} 个任务。"

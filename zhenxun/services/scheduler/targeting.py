"""
目标解析与选择器 (Targeting)

提供用于解析任务目标和批量操作目标的 ScheduleTargeter 类。
"""

from collections.abc import Callable, Coroutine
from typing import Any

from nonebot.adapters import Bot

from zhenxun.services.tags import tag_manager

from .engine import APSchedulerAdapter
from .repository import ScheduleRepository

__all__ = [
    "ScheduleTargeter",
    "_resolve_all_groups",
    "_resolve_global_or_user",
    "_resolve_group",
    "_resolve_tag",
    "_resolve_user",
]


async def _resolve_group(target_identifier: str, bot: Bot) -> list[str | None]:
    """解析 GROUP 类型的执行目标。"""
    return [target_identifier]


async def _resolve_tag(target_identifier: str, bot: Bot) -> list[str | None]:
    """解析 TAG 类型的标签执行目标。"""
    result = await tag_manager.resolve_tag_to_group_ids(target_identifier)
    return result  # type: ignore


async def _resolve_user(target_identifier: str, bot: Bot) -> list[str | None]:
    """解析 USER 类型的执行目标。"""
    return [target_identifier]


async def _resolve_all_groups(target_identifier: str, bot: Bot) -> list[str | None]:
    """解析 ALL_GROUPS 类型的全群执行目标。"""
    result = await tag_manager.resolve_tag_to_group_ids("@all", bot=bot)
    return result


async def _resolve_global_or_user(target_identifier: str, bot: Bot) -> list[str | None]:
    """解析 GLOBAL 类型的全局执行目标。"""
    return [None]


class ScheduleTargeter:
    """
    一个用于构建和执行定时任务批量操作的目标选择器。
    """

    def __init__(self, manager: Any, **filters: Any):
        """
        初始化目标选择器

        参数:
            manager: SchedulerManager 实例。
            **filters: 过滤条件，支持plugin_name、group_id、bot_id等字段。
        """
        self._manager = manager
        self._filters = {k: v for k, v in filters.items() if v is not None}

    async def _get_schedules(self):
        """
        根据过滤器获取任务

        返回:
            list[ScheduledJob]: 符合过滤条件的任务列表。
        """
        query = ScheduleRepository.filter(**self._filters)
        return await query.all()

    def _generate_target_description(self) -> str:
        """
        根据过滤条件生成友好的目标描述

        返回:
            str: 描述目标的友好字符串。
        """
        if "id" in self._filters:
            return f"任务 ID {self._filters['id']} 的"

        parts = []
        if "target_descriptor" in self._filters:
            descriptor = self._filters["target_descriptor"]
            if descriptor == self._manager.ALL_GROUPS:
                parts.append("所有群组中")
            elif descriptor.startswith("tag:"):
                parts.append(f"标签 '{descriptor[4:]}' 的")
            else:
                parts.append(f"群 {descriptor} 中")

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

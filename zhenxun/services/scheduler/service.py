"""
服务层 (Service)

定义 SchedulerManager 类作为定时任务服务的公共 API 入口。
它负责编排业务逻辑，并调用 Repository 和 Adapter 层来完成具体工作。
"""

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, ClassVar

import nonebot
from pydantic import BaseModel

from zhenxun.configs.config import Config
from zhenxun.models.schedule_info import ScheduleInfo
from zhenxun.services.log import logger

from .adapter import APSchedulerAdapter
from .job import _execute_job
from .repository import ScheduleRepository
from .targeter import ScheduleTargeter


class SchedulerManager:
    ALL_GROUPS: ClassVar[str] = "__ALL_GROUPS__"
    _registered_tasks: ClassVar[
        dict[str, dict[str, Callable | type[BaseModel] | None]]
    ] = {}
    _declared_tasks: ClassVar[list[dict[str, Any]]] = []
    _running_tasks: ClassVar[set] = set()

    def target(self, **filters: Any) -> ScheduleTargeter:
        """
        创建目标选择器以执行批量操作

        参数:
            **filters: 过滤条件，支持plugin_name、group_id、bot_id等字段。

        返回:
            ScheduleTargeter: 目标选择器对象，可用于批量操作。
        """
        return ScheduleTargeter(self, **filters)

    def task(
        self,
        trigger: str,
        group_id: str | None = None,
        bot_id: str | None = None,
        **trigger_kwargs,
    ):
        """
        声明式定时任务装饰器

        参数:
            trigger: 触发器类型，如'cron'、'interval'等。
            group_id: 目标群组ID，None表示全局任务。
            bot_id: 目标Bot ID，None表示使用默认Bot。
            **trigger_kwargs: 触发器配置参数。

        返回:
            Callable: 装饰器函数。
        """

        def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
            try:
                plugin = nonebot.get_plugin_by_module_name(func.__module__)
                if not plugin:
                    raise ValueError(f"函数 {func.__name__} 不在任何已加载的插件中。")
                plugin_name = plugin.name

                task_declaration = {
                    "plugin_name": plugin_name,
                    "func": func,
                    "group_id": group_id,
                    "bot_id": bot_id,
                    "trigger_type": trigger,
                    "trigger_config": trigger_kwargs,
                    "job_kwargs": {},
                }
                self._declared_tasks.append(task_declaration)
                logger.debug(
                    f"发现声明式定时任务 '{plugin_name}'，将在启动时进行注册。"
                )
            except Exception as e:
                logger.error(f"注册声明式定时任务失败: {func.__name__}, 错误: {e}")

            return func

        return decorator

    def register(
        self, plugin_name: str, params_model: type[BaseModel] | None = None
    ) -> Callable:
        """
        注册可调度的任务函数

        参数:
            plugin_name: 插件名称，用于标识任务。
            params_model: 参数验证模型，继承自BaseModel的类。

        返回:
            Callable: 装饰器函数。
        """

        def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
            if plugin_name in self._registered_tasks:
                logger.warning(f"插件 '{plugin_name}' 的定时任务已被重复注册。")
            self._registered_tasks[plugin_name] = {
                "func": func,
                "model": params_model,
            }
            model_name = params_model.__name__ if params_model else "无"
            logger.debug(
                f"插件 '{plugin_name}' 的定时任务已注册，参数模型: {model_name}"
            )
            return func

        return decorator

    def get_registered_plugins(self) -> list[str]:
        """
        获取已注册插件列表

        返回:
            list[str]: 已注册的插件名称列表。
        """
        return list(self._registered_tasks.keys())

    async def add_daily_task(
        self,
        plugin_name: str,
        group_id: str | None,
        hour: int,
        minute: int,
        second: int = 0,
        job_kwargs: dict | None = None,
        bot_id: str | None = None,
    ) -> "ScheduleInfo | None":
        """
        添加每日定时任务

        参数:
            plugin_name: 插件名称。
            group_id: 目标群组ID，None表示全局任务。
            hour: 执行小时（0-23）。
            minute: 执行分钟（0-59）。
            second: 执行秒数（0-59），默认为0。
            job_kwargs: 任务参数字典。
            bot_id: 目标Bot ID，None表示使用默认Bot。

        返回:
            ScheduleInfo | None: 创建的任务信息，失败时返回None。
        """
        trigger_config = {
            "hour": hour,
            "minute": minute,
            "second": second,
            "timezone": Config.get_config("SchedulerManager", "SCHEDULER_TIMEZONE"),
        }
        return await self.add_schedule(
            plugin_name,
            group_id,
            "cron",
            trigger_config,
            job_kwargs=job_kwargs,
            bot_id=bot_id,
        )

    async def add_interval_task(
        self,
        plugin_name: str,
        group_id: str | None,
        *,
        weeks: int = 0,
        days: int = 0,
        hours: int = 0,
        minutes: int = 0,
        seconds: int = 0,
        start_date: str | datetime | None = None,
        job_kwargs: dict | None = None,
        bot_id: str | None = None,
    ) -> "ScheduleInfo | None":
        """添加间隔性定时任务"""
        trigger_config = {
            "weeks": weeks,
            "days": days,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
            "start_date": start_date,
        }
        trigger_config = {k: v for k, v in trigger_config.items() if v}
        return await self.add_schedule(
            plugin_name,
            group_id,
            "interval",
            trigger_config,
            job_kwargs=job_kwargs,
            bot_id=bot_id,
        )

    def _validate_and_prepare_kwargs(
        self, plugin_name: str, job_kwargs: dict | None
    ) -> tuple[bool, str | dict]:
        """验证并准备任务参数，应用默认值"""
        from pydantic import ValidationError

        task_meta = self._registered_tasks.get(plugin_name)
        if not task_meta:
            return False, f"插件 '{plugin_name}' 未注册。"

        params_model = task_meta.get("model")
        job_kwargs = job_kwargs if job_kwargs is not None else {}

        if not params_model:
            if job_kwargs:
                logger.warning(
                    f"插件 '{plugin_name}' 未定义参数模型，但收到了参数: {job_kwargs}"
                )
            return True, job_kwargs

        if not (isinstance(params_model, type) and issubclass(params_model, BaseModel)):
            logger.error(f"插件 '{plugin_name}' 的参数模型不是有效的 BaseModel 类")
            return False, f"插件 '{plugin_name}' 的参数模型配置错误"

        try:
            model_validate = getattr(params_model, "model_validate", None)
            if not model_validate:
                return False, f"插件 '{plugin_name}' 的参数模型不支持验证"

            validated_model = model_validate(job_kwargs)

            model_dump = getattr(validated_model, "model_dump", None)
            if not model_dump:
                return False, f"插件 '{plugin_name}' 的参数模型不支持导出"

            return True, model_dump()
        except ValidationError as e:
            errors = [f"  - {err['loc'][0]}: {err['msg']}" for err in e.errors()]
            error_str = "\n".join(errors)
            msg = f"插件 '{plugin_name}' 的任务参数验证失败:\n{error_str}"
            return False, msg

    async def add_schedule(
        self,
        plugin_name: str,
        group_id: str | None,
        trigger_type: str,
        trigger_config: dict,
        job_kwargs: dict | None = None,
        bot_id: str | None = None,
    ) -> "ScheduleInfo | None":
        """
        添加定时任务（通用方法）

        参数:
            plugin_name: 插件名称。
            group_id: 目标群组ID，None表示全局任务。
            trigger_type: 触发器类型，如'cron'、'interval'等。
            trigger_config: 触发器配置字典。
            job_kwargs: 任务参数字典。
            bot_id: 目标Bot ID，None表示使用默认Bot。

        返回:
            ScheduleInfo | None: 创建的任务信息，失败时返回None。
        """
        if plugin_name not in self._registered_tasks:
            logger.error(f"插件 '{plugin_name}' 没有注册可用的定时任务。")
            return None

        is_valid, result = self._validate_and_prepare_kwargs(plugin_name, job_kwargs)
        if not is_valid:
            logger.error(f"任务参数校验失败: {result}")
            return None

        search_kwargs = {"plugin_name": plugin_name, "group_id": group_id}
        if bot_id and group_id == self.ALL_GROUPS:
            search_kwargs["bot_id"] = bot_id
        else:
            search_kwargs["bot_id__isnull"] = True

        defaults = {
            "trigger_type": trigger_type,
            "trigger_config": trigger_config,
            "job_kwargs": result,
            "is_enabled": True,
        }

        schedule, created = await ScheduleRepository.update_or_create(
            defaults, **search_kwargs
        )
        APSchedulerAdapter.add_or_reschedule_job(schedule)

        action = "设置" if created else "更新"
        logger.info(
            f"已成功{action}插件 '{plugin_name}' 的定时任务 (ID: {schedule.id})。"
        )
        return schedule

    async def get_all_schedules(self) -> list[ScheduleInfo]:
        """
        获取所有定时任务信息
        """
        return await self.get_schedules()

    async def get_schedules(
        self,
        plugin_name: str | None = None,
        group_id: str | None = None,
        bot_id: str | None = None,
    ) -> list[ScheduleInfo]:
        """
        根据条件获取定时任务列表

        参数:
            plugin_name: 插件名称，None表示不限制。
            group_id: 群组ID，None表示不限制。
            bot_id: Bot ID，None表示不限制。

        返回:
            list[ScheduleInfo]: 符合条件的任务信息列表。
        """
        return await ScheduleRepository.query_schedules(
            plugin_name=plugin_name, group_id=group_id, bot_id=bot_id
        )

    async def update_schedule(
        self,
        schedule_id: int,
        trigger_type: str | None = None,
        trigger_config: dict | None = None,
        job_kwargs: dict | None = None,
    ) -> tuple[bool, str]:
        """
        更新定时任务配置

        参数:
            schedule_id: 任务ID。
            trigger_type: 新的触发器类型，None表示不更新。
            trigger_config: 新的触发器配置，None表示不更新。
            job_kwargs: 新的任务参数，None表示不更新。

        返回:
            tuple[bool, str]: (是否成功, 结果消息)。
        """
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule:
            return False, f"未找到 ID 为 {schedule_id} 的任务。"

        updated_fields = []
        if trigger_config is not None:
            schedule.trigger_config = trigger_config
            updated_fields.append("trigger_config")
            if trigger_type is not None and schedule.trigger_type != trigger_type:
                schedule.trigger_type = trigger_type
                updated_fields.append("trigger_type")

        if job_kwargs is not None:
            existing_kwargs = (
                schedule.job_kwargs.copy()
                if isinstance(schedule.job_kwargs, dict)
                else {}
            )
            existing_kwargs.update(job_kwargs)

            is_valid, result = self._validate_and_prepare_kwargs(
                schedule.plugin_name, existing_kwargs
            )
            if not is_valid:
                return False, str(result)

            assert isinstance(result, dict), "验证成功时 result 应该是字典类型"
            schedule.job_kwargs = result
            updated_fields.append("job_kwargs")

        if not updated_fields:
            return True, "没有任何需要更新的配置。"

        await ScheduleRepository.save(schedule, update_fields=updated_fields)
        APSchedulerAdapter.add_or_reschedule_job(schedule)
        return True, f"成功更新了任务 ID: {schedule_id} 的配置。"

    async def get_schedule_status(self, schedule_id: int) -> dict | None:
        """获取定时任务的详细状态信息"""
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule:
            return None

        status_from_scheduler = APSchedulerAdapter.get_job_status(schedule.id)

        status_text = (
            "运行中"
            if schedule_id in self._running_tasks
            else ("启用" if schedule.is_enabled else "暂停")
        )

        return {
            "id": schedule.id,
            "bot_id": schedule.bot_id,
            "plugin_name": schedule.plugin_name,
            "group_id": schedule.group_id,
            "is_enabled": status_text,
            "trigger_type": schedule.trigger_type,
            "trigger_config": schedule.trigger_config,
            "job_kwargs": schedule.job_kwargs,
            **status_from_scheduler,
        }

    async def pause_schedule(self, schedule_id: int) -> tuple[bool, str]:
        """暂停指定的定时任务"""
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule or not schedule.is_enabled:
            return False, "任务不存在或已暂停。"

        schedule.is_enabled = False
        await ScheduleRepository.save(schedule, update_fields=["is_enabled"])
        APSchedulerAdapter.pause_job(schedule_id)
        return True, f"已暂停任务 (ID: {schedule.id})。"

    async def resume_schedule(self, schedule_id: int) -> tuple[bool, str]:
        """恢复指定的定时任务"""
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule or schedule.is_enabled:
            return False, "任务不存在或已启用。"

        schedule.is_enabled = True
        await ScheduleRepository.save(schedule, update_fields=["is_enabled"])
        APSchedulerAdapter.resume_job(schedule_id)
        return True, f"已恢复任务 (ID: {schedule.id})。"

    async def trigger_now(self, schedule_id: int) -> tuple[bool, str]:
        """立即手动触发指定的定时任务"""
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule:
            return False, f"未找到 ID 为 {schedule_id} 的定时任务。"
        if schedule.plugin_name not in self._registered_tasks:
            return False, f"插件 '{schedule.plugin_name}' 没有注册可用的定时任务。"

        try:
            await _execute_job(schedule.id)
            return True, f"已手动触发任务 (ID: {schedule.id})。"
        except Exception as e:
            logger.error(f"手动触发任务失败: {e}")
            return False, f"手动触发任务失败: {e}"


scheduler_manager = SchedulerManager()

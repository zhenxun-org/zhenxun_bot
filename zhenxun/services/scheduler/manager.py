"""
服务层 (Service Manager)

定义 SchedulerManager 类作为定时任务服务的公共 API 入口。
它负责编排业务逻辑，并调用 Repository 和 Adapter 层来完成具体工作。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
import inspect
import json
from typing import Any, ClassVar
import uuid

from arclet.alconna import Alconna
import nonebot
from nonebot.adapters import Bot
from pydantic import BaseModel, ValidationError

from zhenxun.configs.config import Config
from zhenxun.models.scheduled_job import ScheduledJob
from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import dump_json_safely, model_dump, model_validate

from .engine import APSchedulerAdapter, _execute_persistent_job
from .registry import scheduler_registry
from .repository import ScheduleRepository
from .targeting import (
    ScheduleTargeter,
    _resolve_all_groups,
    _resolve_global_or_user,
    _resolve_group,
    _resolve_tag,
    _resolve_user,
)
from .types import (
    BaseTrigger,
    EphemeralJobDeclaration,
    ExecutionPolicy,
    JobConfig,
    ScheduleContext,
    ScheduledJobDeclaration,
    TargetType,
    Trigger,
)


class SchedulerManager:
    ALL_GROUPS: ClassVar[str] = scheduler_registry.ALL_GROUPS

    def __init__(self):
        self._register_builtin_resolvers()

    def _register_builtin_resolvers(self):
        """在管理器初始化时注册所有内置的目标解析器。"""
        if TargetType.GROUP.value in scheduler_registry.target_resolvers:
            return
        scheduler_registry.register_target_resolver(
            TargetType.GROUP.value, _resolve_group
        )
        scheduler_registry.register_target_resolver(TargetType.TAG.value, _resolve_tag)
        scheduler_registry.register_target_resolver(
            TargetType.ALL_GROUPS.value, _resolve_all_groups
        )
        scheduler_registry.register_target_resolver(
            TargetType.GLOBAL.value, _resolve_global_or_user
        )
        scheduler_registry.register_target_resolver(
            TargetType.USER.value, _resolve_user
        )
        logger.debug("已注册所有内置的定时任务目标解析器。")

    def register_target_resolver(
        self,
        target_type: str,
        resolver_func: Callable[[str, Bot], Awaitable[list[str | None]]],
    ):
        """
        注册一个新的目标类型解析器。
        """
        if target_type in scheduler_registry.target_resolvers:
            logger.warning(f"目标解析器 '{target_type}' 已存在，将被覆盖。")
        scheduler_registry.register_target_resolver(target_type, resolver_func)
        logger.debug(f"已注册新的定时任务目标解析器: '{target_type}'")

    def target(self, **filters: Any) -> ScheduleTargeter:
        """
        创建目标选择器以执行批量操作
        """
        return ScheduleTargeter(self, **filters)

    def job(
        self,
        trigger: BaseTrigger,
        group_id: str | None = None,
        bot_id: str | None = None,
        default_params: BaseModel | None = None,
        policy: ExecutionPolicy | None = None,
        default_jitter: int | None = None,
        default_spread: int | None = None,
        default_interval: int | None = None,
    ):
        """
        声明式定时任务的统一装饰器

        参数:
            trigger: 定时触发器配置。
            group_id: 默认目标群号，如果不指定则为全局任务。
            bot_id: 执行此任务时应优先匹配的 Bot 标识符。
            default_params: 任务函数的默认参数模型实例。
            policy: 执行和重试策略配置。
            default_jitter: 默认抖动延迟时间（秒）。
            default_spread: 默认并发散列随机延迟打散的最大延迟范围（秒）。
            default_interval: 默认串行派发任务时的固定等待间隔（秒）。

        返回:
            Callable: 装饰器函数，用于包裹并注册声明式任务。
        """

        def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
            try:
                plugin = nonebot.get_plugin_by_module_name(func.__module__)
                if not plugin:
                    raise ValueError(f"函数 {func.__name__} 不在任何已加载的插件中。")
                plugin_name = plugin.name

                params_model = None

                for param in inspect.signature(func).parameters.values():
                    if (
                        isinstance(param.annotation, type)
                        and issubclass(param.annotation, BaseModel)
                        and param.annotation is not ScheduleContext
                    ):
                        params_model = param.annotation
                        break

                if plugin_name in scheduler_registry.tasks:
                    logger.warning(f"插件 '{plugin_name}' 的定时任务已被重复注册。")
                scheduler_registry.tasks[plugin_name] = {
                    "func": func,
                    "model": params_model,
                    "default_jitter": default_jitter,
                    "default_spread": default_spread,
                    "default_interval": default_interval,
                }

                job_kwargs = model_dump(default_params) if default_params else {}
                if policy:
                    job_kwargs["execution_policy"] = model_dump(policy)

                task_declaration = ScheduledJobDeclaration(
                    plugin_name=plugin_name,
                    group_id=group_id,
                    bot_id=bot_id,
                    trigger=trigger,
                    job_kwargs=job_kwargs,
                )
                scheduler_registry.persistent_declarations.append(task_declaration)
                logger.debug(
                    f"发现声明式定时任务 '{plugin_name}'，将在启动时进行注册。"
                )
            except Exception as e:
                logger.error(f"注册声明式定时任务失败: {func.__name__}, 错误: {e}")

            return func

        return decorator

    def runtime_job(self, trigger: BaseTrigger):
        """
        声明一个临时的、非持久化的定时任务。
        """

        def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
            try:
                plugin = nonebot.get_plugin_by_module_name(func.__module__)
                if not plugin:
                    raise ValueError(f"函数 {func.__name__} 不在任何已加载的插件中。")
                plugin_name = plugin.name

                scheduler_registry.tasks[
                    f"ephemeral::{plugin_name}::{func.__name__}"
                ] = {
                    "func": func,
                    "model": None,
                }

                declaration = EphemeralJobDeclaration(
                    plugin_name=plugin_name,
                    func=func,
                    trigger=trigger,
                )
                scheduler_registry.ephemeral_declarations.append(declaration)
                logger.debug(
                    f"发现临时定时任务 '{plugin_name}:{func.__name__}'，将在启动时调度"
                )
            except Exception as e:
                logger.error(f"注册临时定时任务失败: {func.__name__}, 错误: {e}")

            return func

        return decorator

    def register(
        self,
        plugin_name: str,
        params_model: type[BaseModel] | None = None,
        cli_parser: Alconna | None = None,
        default_permission: int = 5,
        default_jitter: int | None = None,
        default_spread: int | None = None,
        default_interval: int | None = None,
    ) -> Callable:
        """
        注册可调度的任务函数

        参数:
            plugin_name: 插件名称。
            params_model: 参数的模型定义，如为 None 则任务不接收额外参数。
            cli_parser: 用于解析命令行参数的 Alconna 解析器。
            default_permission: 运行此定时任务所需的默认权限等级。
            default_jitter: 默认抖动延迟时间（秒）。
            default_spread: 默认并发散列随机延迟打散的最大延迟范围（秒）。
            default_interval: 默认串行派发任务时的固定等待间隔（秒）。

        返回:
            Callable: 装饰器函数，用于包裹并注册任务。
        """

        def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
            if plugin_name in scheduler_registry.tasks:
                logger.warning(f"插件 '{plugin_name}' 的定时任务已被重复注册。")
            scheduler_registry.tasks[plugin_name] = {
                "func": func,
                "model": params_model,
                "cli_parser": cli_parser,
                "default_permission": default_permission,
                "default_jitter": default_jitter,
                "default_spread": default_spread,
                "default_interval": default_interval,
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
        """
        return list(scheduler_registry.tasks.keys())

    async def run_at(self, func: Callable[..., Coroutine], trigger: BaseTrigger) -> str:
        """
        在未来的某个时间点，运行一个一次性的临时任务。
        """

        job_id = f"ephemeral_runtime_{uuid.uuid4()}"

        context = ScheduleContext(
            schedule_id=0,
            plugin_name=f"runtime::{func.__module__}",
            bot_id=None,
            platform_scope=None,
            group_id=None,
            user_id=None,
            job_kwargs={},
        )

        trigger_config_dict = json.loads(dump_json_safely(trigger))
        trigger_config_dict.pop("trigger_type", None)

        APSchedulerAdapter.add_ephemeral_job(
            job_id=job_id,
            func=func,
            trigger_type=trigger.trigger_type,
            trigger_config=trigger_config_dict,
            context=context,
        )
        logger.info(f"已动态调度一个临时任务 (ID: {job_id})，将在 {trigger} 触发。")
        return job_id

    async def schedule_once(
        self,
        func: Callable[..., Coroutine],
        trigger: BaseTrigger,
        *,
        user_id: str | None = None,
        group_id: str | None = None,
        bot_id: str | None = None,
        job_kwargs: dict | None = None,
        name: str | None = None,
        created_by: str | None = None,
        required_permission: int = 5,
    ) -> "ScheduledJob | None":
        """
        编程式API，用于动态调度一个持久化的、一次性的任务

        参数:
            func: 待执行的协程函数。
            trigger: 定时触发器配置。
            user_id: 目标用户 ID，与 group_id 互斥。
            group_id: 目标群号，与 user_id 互斥。
            bot_id: 指定运行此任务的 Bot 标识符。
            job_kwargs: 传递给任务函数的实际参数字典。
            name: 任务的可读别名。
            created_by: 任务创建者的标识。
            required_permission: 运行该任务需要的最低权限等级。

        返回:
            ScheduledJob | None: 持久化定时任务的数据模型实例，如果失败则返回 None。
        """
        if user_id and group_id:
            raise ValueError("user_id 和 group_id 不能同时提供。")

        temp_plugin_name = f"runtime_one_off__{func.__module__}.{func.__name__}__{uuid.uuid4().hex[:8]}"  # noqa: E501

        scheduler_registry.tasks[temp_plugin_name] = {"func": func, "model": None}
        logger.debug(f"为一次性任务动态注册临时插件: '{temp_plugin_name}'")

        target_type = (
            TargetType.USER.value
            if user_id
            else (TargetType.GROUP.value if group_id else TargetType.GLOBAL.value)
        )
        target_identifier = user_id or group_id or ""

        config = JobConfig(
            trigger=trigger,
            job_kwargs=job_kwargs or {},
            bot_id=bot_id,
            name=name,
            created_by=created_by,
            required_permission=required_permission,
            is_one_off=True,
        )

        return await self.add_schedule(
            plugin_name=temp_plugin_name,
            target_type=target_type,
            target_identifier=target_identifier,
            config=config,
        )

    async def add_daily_task(
        self,
        plugin_name: str,
        group_id: str | None,
        hour: int,
        minute: int,
        second: int = 0,
        job_kwargs: dict | None = None,
        bot_id: str | None = None,
    ) -> "ScheduledJob | None":
        """
        添加每日定时任务

        参数:
            plugin_name: 插件名称。
            group_id: 目标群号，如果不指定则为全局任务。
            hour: 触发的小时数（0-23）。
            minute: 触发的分钟数（0-59）。
            second: 触发的秒数（0-59）。
            job_kwargs: 传递给任务函数的参数字典。
            bot_id: 运行此任务的首选 Bot。

        返回:
            ScheduledJob | None: 定时任务的数据模型对象，如果失败则返回 None。
        """
        trigger_config = {
            "hour": hour,
            "minute": minute,
            "second": second,
            "timezone": Config.get_config("SchedulerManager", "SCHEDULER_TIMEZONE"),
        }

        trigger = Trigger.cron(**trigger_config)
        return await self.add_schedule(
            plugin_name,
            target_type=TargetType.GROUP.value if group_id else TargetType.GLOBAL.value,
            target_identifier=group_id or "",
            config=JobConfig(
                trigger=trigger, job_kwargs=job_kwargs or {}, bot_id=bot_id
            ),
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
    ) -> "ScheduledJob | None":
        """
        添加间隔性定时任务

        参数:
            plugin_name: 插件名称。
            group_id: 目标群号，如果不指定则为全局任务。
            weeks: 间隔的周数。
            days: 间隔的天数。
            hours: 间隔的小时数。
            minutes: 间隔的分钟数。
            seconds: 间隔的秒数。
            start_date: 间隔计算的起始时间。
            job_kwargs: 传递给任务函数的参数字典。
            bot_id: 运行此任务的首选 Bot。

        返回:
            ScheduledJob | None: 定时任务的数据模型对象，如果失败则返回 None。
        """
        trigger_config = {
            "weeks": weeks,
            "days": days,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
            "start_date": start_date,
        }
        trigger_config = {k: v for k, v in trigger_config.items() if v}

        trigger = Trigger.interval(**trigger_config)
        return await self.add_schedule(
            plugin_name,
            target_type=TargetType.GROUP.value if group_id else TargetType.GLOBAL.value,
            target_identifier=group_id or "",
            config=JobConfig(
                trigger=trigger, job_kwargs=job_kwargs or {}, bot_id=bot_id
            ),
        )

    def _validate_and_prepare_kwargs(
        self, plugin_name: str, job_kwargs: dict | None
    ) -> tuple[bool, str | dict]:
        """验证并准备任务参数，应用默认值"""
        task_meta = scheduler_registry.tasks.get(plugin_name)
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
            validated_model = model_validate(params_model, job_kwargs)

            return True, model_dump(validated_model)
        except ValidationError as e:
            errors = [f"  - {err['loc'][0]}: {err['msg']}" for err in e.errors()]
            error_str = "\n".join(errors)
            msg = f"插件 '{plugin_name}' 的任务参数验证失败:\n{error_str}"
            return False, msg

    async def add_schedule(
        self,
        plugin_name: str,
        target_type: str,
        target_identifier: str,
        config: JobConfig,
    ) -> "ScheduledJob | None":
        """
        添加定时任务（通用方法）

        参数:
            plugin_name: 插件名称。
            target_type: 目标类型 (GROUP, USER, TAG, ALL_GROUPS, GLOBAL)。
            target_identifier: 目标标识符。
            config: JobConfig 参数配置聚合对象。
        """
        if plugin_name not in scheduler_registry.tasks:
            logger.error(f"插件 '{plugin_name}' 没有注册可用的定时任务。")
            return None

        is_valid, result = self._validate_and_prepare_kwargs(
            plugin_name, config.job_kwargs
        )
        if not is_valid:
            logger.error(f"任务参数校验失败: {result}")
            return None

        search_kwargs = {
            "plugin_name": plugin_name,
            "target_type": target_type,
            "target_identifier": target_identifier,
        }
        if config.bot_id:
            search_kwargs["bot_id"] = config.bot_id

        trigger_config_dict = json.loads(dump_json_safely(config.trigger))
        trigger_config_dict.pop("trigger_type", None)

        defaults = {
            "name": config.name,
            "trigger_type": config.trigger.trigger_type,
            "trigger_config": trigger_config_dict,
            "job_kwargs": result,
            "is_enabled": True,
            "created_by": config.created_by,
            "required_permission": config.required_permission,
            "source": config.source,
            "is_one_off": config.is_one_off,
            "execution_options": model_dump(
                config.execution_options, exclude_none=True
            ),
        }

        defaults = {k: v for k, v in defaults.items() if v is not None}

        schedule, created = await ScheduleRepository.update_or_create(
            defaults, **search_kwargs
        )
        APSchedulerAdapter.add_or_reschedule_job(schedule)

        action_str = "创建" if created else "更新"
        logger.info(
            f"已成功{action_str}任务 '{config.name or plugin_name}' (ID: {schedule.id})"
        )
        return schedule

    async def get_schedules(
        self, page: int | None = None, page_size: int | None = None, **filters: Any
    ) -> tuple[list[ScheduledJob], int]:
        """
        根据条件获取定时任务列表

        参数:
            page: 分页页码，从 1 开始。
            page_size: 每页的任务数量。
            **filters: 过滤条件，支持 plugin_name, target_type, is_enabled 等字段。

        返回:
            tuple[list[ScheduledJob], int]: 包含任务对象列表和总符合条件的任务数量的元组。
        """  # noqa: E501
        cleaned_filters = {k: v for k, v in filters.items() if v is not None}
        return await ScheduleRepository.query_schedules(
            page=page, page_size=page_size, **cleaned_filters
        )

    async def get_schedules_status_bulk(
        self, schedule_ids: list[int]
    ) -> list[dict[str, Any]]:
        """
        批量获取多个定时任务的详细状态信息
        """
        if not schedule_ids:
            return []

        schedules = await ScheduleRepository.filter(id__in=schedule_ids).all()
        schedule_map = {s.id: s for s in schedules}

        statuses = []
        for schedule_id in schedule_ids:
            if schedule := schedule_map.get(schedule_id):
                status_from_scheduler = APSchedulerAdapter.get_job_status(schedule.id)
                status_dict = {
                    field: getattr(schedule, field)
                    for field in schedule._meta.fields_map
                }
                status_dict.update(status_from_scheduler)
                status_dict["is_enabled"] = (
                    "运行中"
                    if schedule_id in scheduler_registry.running_tasks
                    else ("启用" if schedule.is_enabled else "暂停")
                )
                statuses.append(status_dict)

        return statuses

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
            schedule_id: 定时任务的ID。
            trigger_type: 触发器类型，例如 'cron' 或 'interval'。
            trigger_config: 触发器配置字典。
            job_kwargs: 更新后的任务参数字典。

        返回:
            tuple[bool, str]: 包含执行结果（成功为 True）和对应状态消息的元组。
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
        """
        获取定时任务的详细状态信息
        """
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule:
            return None

        status_from_scheduler = APSchedulerAdapter.get_job_status(schedule.id)

        status_text = (
            "运行中"
            if schedule_id in scheduler_registry.running_tasks
            else ("启用" if schedule.is_enabled else "暂停")
        )

        return {
            "id": schedule.id,
            "bot_id": schedule.bot_id,
            "plugin_name": schedule.plugin_name,
            "target_type": schedule.target_type,
            "target_identifier": schedule.target_identifier,
            "is_enabled": status_text,
            "trigger_type": schedule.trigger_type,
            "trigger_config": schedule.trigger_config,
            "job_kwargs": schedule.job_kwargs,
            **status_from_scheduler,
        }

    async def pause_schedule(self, schedule_id: int) -> tuple[bool, str]:
        """
        暂停指定的定时任务
        """
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule or not schedule.is_enabled:
            return False, "任务不存在或已暂停。"

        schedule.is_enabled = False
        await ScheduleRepository.save(schedule, update_fields=["is_enabled"])
        APSchedulerAdapter.pause_job(schedule_id)
        return True, f"已暂停任务 (ID: {schedule.id})。"

    async def resume_schedule(self, schedule_id: int) -> tuple[bool, str]:
        """
        恢复指定的定时任务
        """
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule or schedule.is_enabled:
            return False, "任务不存在或已启用。"

        schedule.is_enabled = True
        await ScheduleRepository.save(schedule, update_fields=["is_enabled"])
        APSchedulerAdapter.resume_job(schedule_id)
        return True, f"已恢复任务 (ID: {schedule.id})。"

    async def trigger_now(self, schedule_id: int) -> tuple[bool, str]:
        """
        立即手动触发指定的定时任务
        """
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule:
            return False, f"未找到 ID 为 {schedule_id} 的定时任务。"
        if schedule.plugin_name not in scheduler_registry.tasks:
            return False, f"插件 '{schedule.plugin_name}' 没有注册可用的定时任务。"

        try:
            await _execute_persistent_job(schedule.id, force=True)
            return True, f"已手动触发任务 (ID: {schedule.id})。"
        except Exception as e:
            logger.error(f"手动触发任务失败: {e}")
            return False, f"手动触发任务失败: {e}"

    async def get_schedule_by_id(self, schedule_id: int) -> "ScheduledJob | None":
        """
        通过ID获取任务对象的公共方法。
        """
        return await ScheduleRepository.get_by_id(schedule_id)


scheduler_manager = SchedulerManager()

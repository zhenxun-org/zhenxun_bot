"""
服务层 (Service Manager)

定义 SchedulerManager 类作为定时任务服务的公共 API 入口。
它负责编排业务逻辑，并调用 Repository 和 Adapter 层来完成具体工作。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
import inspect
from typing import Any, ClassVar
import uuid

from arclet.alconna import Alconna, Option
import nonebot
from nonebot.adapters import Bot
from pydantic import BaseModel

from zhenxun.configs.config import Config
from zhenxun.models.scheduled_job import ScheduledJob
from zhenxun.services.log import logger
from zhenxun.utils.pydantic_compat import model_dump, model_validate

from .engine import APSchedulerAdapter
from .repository import ScheduleRepository
from .targeting import (
    ScheduleTargeter,
)
from .types import (
    BaseTrigger,
    EphemeralJobDeclaration,
    ExecutionOptions,
    ExecutionPolicy,
    ScheduleContext,
    ScheduledJobDeclaration,
)


class SchedulerManager:
    ALL_GROUPS: ClassVar[str] = "__ALL_GROUPS__"
    _registered_tasks: ClassVar[
        dict[
            str,
            dict[str, Callable | type[BaseModel] | int | list[Option] | Alconna | None],
        ]
    ] = {}
    _declared_tasks: ClassVar[list[ScheduledJobDeclaration]] = []
    _ephemeral_declared_tasks: ClassVar[list[EphemeralJobDeclaration]] = []
    _running_tasks: ClassVar[set] = set()
    _target_resolvers: ClassVar[
        dict[str, Callable[[str, Bot], Awaitable[list[str | None]]]]
    ] = {}

    def __init__(self):
        self._register_builtin_resolvers()

    def _register_builtin_resolvers(self):
        """在管理器初始化时注册所有内置的目标解析器。"""
        from .targeting import (
            _resolve_all_groups,
            _resolve_global_or_user,
            _resolve_group,
            _resolve_tag,
            _resolve_user,
        )

        if "GROUP" in self._target_resolvers:
            return
        self.register_target_resolver("GROUP", _resolve_group)
        self.register_target_resolver("TAG", _resolve_tag)
        self.register_target_resolver("ALL_GROUPS", _resolve_all_groups)
        self.register_target_resolver("GLOBAL", _resolve_global_or_user)
        self.register_target_resolver("USER", _resolve_user)
        logger.debug("已注册所有内置的定时任务目标解析器。")

    def register_target_resolver(
        self,
        target_type: str,
        resolver_func: Callable[[str, Bot], Awaitable[list[str | None]]],
    ):
        """
        注册一个新的目标类型解析器。
        """
        if target_type in self._target_resolvers:
            logger.warning(f"目标解析器 '{target_type}' 已存在，将被覆盖。")
        self._target_resolvers[target_type.upper()] = resolver_func
        logger.info(f"已注册新的定时任务目标解析器: '{target_type}'")

    def target(self, **filters: Any) -> ScheduleTargeter:
        """
        创建目标选择器以执行批量操作

        参数:
            **filters: 过滤条件，支持plugin_name、group_id、bot_id等字段。

        返回:
            ScheduleTargeter: 目标选择器对象，可用于批量操作。
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
        声明式定时任务的统一装饰器。
        """

        def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
            try:
                plugin = nonebot.get_plugin_by_module_name(func.__module__)
                if not plugin:
                    raise ValueError(f"函数 {func.__name__} 不在任何已加载的插件中。")
                plugin_name = plugin.name

                params_model = None
                from .types import ScheduleContext

                for param in inspect.signature(func).parameters.values():
                    if (
                        isinstance(param.annotation, type)
                        and issubclass(param.annotation, BaseModel)
                        and param.annotation is not ScheduleContext
                    ):
                        params_model = param.annotation
                        break

                if plugin_name in self._registered_tasks:
                    logger.warning(f"插件 '{plugin_name}' 的定时任务已被重复注册。")
                self._registered_tasks[plugin_name] = {
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
                self._declared_tasks.append(task_declaration)
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

                self._registered_tasks[f"ephemeral::{plugin_name}::{func.__name__}"] = {
                    "func": func,
                    "model": None,
                }

                declaration = EphemeralJobDeclaration(
                    plugin_name=plugin_name,
                    func=func,
                    trigger=trigger,
                )
                self._ephemeral_declared_tasks.append(declaration)
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
        """

        def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
            if plugin_name in self._registered_tasks:
                logger.warning(f"插件 '{plugin_name}' 的定时任务已被重复注册。")
            self._registered_tasks[plugin_name] = {
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
        return list(self._registered_tasks.keys())

    async def run_at(self, func: Callable[..., Coroutine], trigger: BaseTrigger) -> str:
        """
        在未来的某个时间点，运行一个一次性的临时任务。
        """

        job_id = f"ephemeral_runtime_{uuid.uuid4()}"

        context = ScheduleContext(
            schedule_id=0,
            plugin_name=f"runtime::{func.__module__}",
            bot_id=None,
            group_id=None,
            job_kwargs={},
        )

        APSchedulerAdapter.add_ephemeral_job(
            job_id=job_id,
            func=func,
            trigger_type=trigger.trigger_type,
            trigger_config=model_dump(trigger, exclude={"trigger_type"}),
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
        编程式API，用于动态调度一个持久化的、一次性的任务。
        """
        if user_id and group_id:
            raise ValueError("user_id 和 group_id 不能同时提供。")

        temp_plugin_name = f"runtime_one_off__{func.__module__}.{func.__name__}__{uuid.uuid4().hex[:8]}"  # noqa: E501

        self._registered_tasks[temp_plugin_name] = {"func": func, "model": None}
        logger.debug(f"为一次性任务动态注册临时插件: '{temp_plugin_name}'")

        target_type = "USER" if user_id else ("GROUP" if group_id else "GLOBAL")
        target_identifier = user_id or group_id or ""

        return await self.add_schedule(
            plugin_name=temp_plugin_name,
            target_type=target_type,
            target_identifier=target_identifier,
            trigger_type=trigger.trigger_type,
            trigger_config=model_dump(trigger, exclude={"trigger_type"}),
            job_kwargs=job_kwargs,
            bot_id=bot_id,
            name=name,
            created_by=created_by,
            required_permission=required_permission,
            is_one_off=True,
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
        """
        trigger_config = {
            "hour": hour,
            "minute": minute,
            "second": second,
            "timezone": Config.get_config("SchedulerManager", "SCHEDULER_TIMEZONE"),
        }
        return await self.add_schedule(
            plugin_name,
            target_type="GROUP" if group_id else "GLOBAL",
            target_identifier=group_id or "",
            trigger_type="cron",
            trigger_config=trigger_config,
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
    ) -> "ScheduledJob | None":
        """
        添加间隔性定时任务
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
        return await self.add_schedule(
            plugin_name,
            target_type="GROUP" if group_id else "GLOBAL",
            target_identifier=group_id or "",
            trigger_type="interval",
            trigger_config=trigger_config,
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
        trigger_type: str,
        trigger_config: dict,
        job_kwargs: dict | None = None,
        bot_id: str | None = None,
        *,
        name: str | None = None,
        created_by: str | None = None,
        required_permission: int = 5,
        source: str = "USER",
        is_one_off: bool = False,
        execution_options: dict | None = None,
    ) -> "ScheduledJob | None":
        """
        添加定时任务（通用方法）

        参数:
            plugin_name: 插件名称。
            target_type: 目标类型 (GROUP, USER, TAG, ALL_GROUPS, GLOBAL)。
            target_identifier: 目标标识符。
            trigger_type: 触发器类型 (cron, interval, date)。
            trigger_config: 触发器配置字典。
            job_kwargs: 传递给任务函数的额外参数。
            bot_id: Bot ID约束。
            name: 任务别名。
            created_by: 创建者ID。
            required_permission: 管理此任务所需的权限。
            source: 任务来源 (USER, PLUGIN_DEFAULT)。
            is_one_off: 是否为一次性任务。
            execution_options: 任务执行的额外选项 (例如: jitter, spread)。

        返回:
            ScheduledJob | None: 创建的任务信息，失败时返回None。
        """
        if plugin_name not in self._registered_tasks:
            logger.error(f"插件 '{plugin_name}' 没有注册可用的定时任务。")
            return None

        is_valid, result = self._validate_and_prepare_kwargs(plugin_name, job_kwargs)
        if not is_valid:
            logger.error(f"任务参数校验失败: {result}")
            return None

        options_dict = execution_options or {}
        validated_options = ExecutionOptions(**options_dict)

        search_kwargs = {
            "plugin_name": plugin_name,
            "target_type": target_type,
            "target_identifier": target_identifier,
        }
        if bot_id:
            search_kwargs["bot_id"] = bot_id

        defaults = {
            "name": name,
            "trigger_type": trigger_type,
            "trigger_config": trigger_config,
            "job_kwargs": result,
            "is_enabled": True,
            "created_by": created_by,
            "required_permission": required_permission,
            "source": source,
            "is_one_off": is_one_off,
            "execution_options": model_dump(validated_options, exclude_none=True),
        }

        defaults = {k: v for k, v in defaults.items() if v is not None}

        schedule, created = await ScheduleRepository.update_or_create(
            defaults, **search_kwargs
        )
        APSchedulerAdapter.add_or_reschedule_job(schedule)

        action_str = "创建" if created else "更新"
        logger.info(
            f"已成功{action_str}任务 '{name or plugin_name}' (ID: {schedule.id})"
        )
        return schedule

    async def get_schedules(
        self, page: int | None = None, page_size: int | None = None, **filters: Any
    ) -> tuple[list[ScheduledJob], int]:
        """
        根据条件获取定时任务列表
        """
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
                    if schedule_id in self._running_tasks
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
            if schedule_id in self._running_tasks
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
        from .engine import _execute_job

        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule:
            return False, f"未找到 ID 为 {schedule_id} 的定时任务。"
        if schedule.plugin_name not in self._registered_tasks:
            return False, f"插件 '{schedule.plugin_name}' 没有注册可用的定时任务。"

        try:
            await _execute_job(schedule.id, force=True)
            return True, f"已手动触发任务 (ID: {schedule.id})。"
        except Exception as e:
            logger.error(f"手动触发任务失败: {e}")
            return False, f"手动触发任务失败: {e}"

    async def get_schedule_by_id(self, schedule_id: int) -> "ScheduledJob | None":
        """
        通过ID获取任务对象的公共方法。

        参数:
            schedule_id: 任务ID。

        返回:
            ScheduledJob | None: 任务对象，不存在时返回None。
        """
        return await ScheduleRepository.get_by_id(schedule_id)


scheduler_manager = SchedulerManager()

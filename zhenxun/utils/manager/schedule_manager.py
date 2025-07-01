import asyncio
from collections.abc import Callable, Coroutine
import copy
import inspect
import random
from typing import ClassVar

import nonebot
from nonebot import get_bots
from nonebot_plugin_apscheduler import scheduler
from pydantic import BaseModel, ValidationError

from zhenxun.configs.config import Config
from zhenxun.models.schedule_info import ScheduleInfo
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.manager.priority_manager import PriorityLifecycle
from zhenxun.utils.platform import PlatformUtils

SCHEDULE_CONCURRENCY_KEY = "all_groups_concurrency_limit"


class SchedulerManager:
    """
    一个通用的、持久化的定时任务管理器，供所有插件使用。
    """

    _registered_tasks: ClassVar[
        dict[str, dict[str, Callable | type[BaseModel] | None]]
    ] = {}
    _JOB_PREFIX = "zhenxun_schedule_"
    _running_tasks: ClassVar[set] = set()

    def register(
        self, plugin_name: str, params_model: type[BaseModel] | None = None
    ) -> Callable:
        """
        注册一个可调度的任务函数。
        被装饰的函数签名应为 `async def func(group_id: str | None, **kwargs)`

        Args:
            plugin_name (str): 插件的唯一名称 (通常是模块名)。
            params_model (type[BaseModel], optional): 一个 Pydantic BaseModel 类，
                用于定义和验证任务函数接受的额外参数。
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
        """获取所有已注册定时任务的插件列表。"""
        return list(self._registered_tasks.keys())

    def _get_job_id(self, schedule_id: int) -> str:
        """根据数据库ID生成唯一的 APScheduler Job ID。"""
        return f"{self._JOB_PREFIX}{schedule_id}"

    async def _execute_job(self, schedule_id: int):
        """
        APScheduler 调度的入口函数。
        根据 schedule_id 处理特定任务、所有群组任务或全局任务。
        """
        schedule = await ScheduleInfo.get_or_none(id=schedule_id)
        if not schedule or not schedule.is_enabled:
            logger.warning(f"定时任务 {schedule_id} 不存在或已禁用，跳过执行。")
            return

        plugin_name = schedule.plugin_name

        task_meta = self._registered_tasks.get(plugin_name)
        if not task_meta:
            logger.error(
                f"无法执行定时任务：插件 '{plugin_name}' 未注册或已卸载。将禁用该任务。"
            )
            schedule.is_enabled = False
            await schedule.save(update_fields=["is_enabled"])
            self._remove_aps_job(schedule.id)
            return

        try:
            if schedule.bot_id:
                bot = nonebot.get_bot(schedule.bot_id)
            else:
                bot = nonebot.get_bot()
                logger.debug(
                    f"任务 {schedule_id} 未关联特定Bot，使用默认Bot {bot.self_id}"
                )
        except KeyError:
            logger.warning(
                f"定时任务 {schedule_id} 需要的 Bot {schedule.bot_id} "
                f"不在线，本次执行跳过。"
            )
            return
        except ValueError:
            logger.warning(f"当前没有Bot在线，定时任务 {schedule_id} 跳过。")
            return

        if schedule.group_id == "__ALL_GROUPS__":
            await self._execute_for_all_groups(schedule, task_meta, bot)
        else:
            await self._execute_for_single_target(schedule, task_meta, bot)

    async def _execute_for_all_groups(
        self, schedule: ScheduleInfo, task_meta: dict, bot
    ):
        """为所有群组执行任务，并处理优先级覆盖。"""
        plugin_name = schedule.plugin_name

        concurrency_limit = Config.get_config(
            "SchedulerManager", SCHEDULE_CONCURRENCY_KEY, 5
        )
        if not isinstance(concurrency_limit, int) or concurrency_limit <= 0:
            logger.warning(
                f"无效的定时任务并发限制配置 '{concurrency_limit}'，将使用默认值 5。"
            )
            concurrency_limit = 5

        logger.info(
            f"开始执行针对 [所有群组] 的任务 "
            f"(ID: {schedule.id}, 插件: {plugin_name}, Bot: {bot.self_id})，"
            f"并发限制: {concurrency_limit}"
        )

        all_gids = set()
        try:
            group_list, _ = await PlatformUtils.get_group_list(bot)
            all_gids.update(
                g.group_id for g in group_list if g.group_id and not g.channel_id
            )
        except Exception as e:
            logger.error(f"为 'all' 任务获取 Bot {bot.self_id} 的群列表失败", e=e)
            return

        specific_tasks_gids = set(
            await ScheduleInfo.filter(
                plugin_name=plugin_name, group_id__in=list(all_gids)
            ).values_list("group_id", flat=True)
        )

        semaphore = asyncio.Semaphore(concurrency_limit)

        async def worker(gid: str):
            """使用 Semaphore 包装单个群组的任务执行"""
            async with semaphore:
                temp_schedule = copy.deepcopy(schedule)
                temp_schedule.group_id = gid
                await self._execute_for_single_target(temp_schedule, task_meta, bot)
                await asyncio.sleep(random.uniform(0.1, 0.5))

        tasks_to_run = []
        for gid in all_gids:
            if gid in specific_tasks_gids:
                logger.debug(f"群组 {gid} 已有特定任务，跳过 'all' 任务的执行。")
                continue
            tasks_to_run.append(worker(gid))

        if tasks_to_run:
            await asyncio.gather(*tasks_to_run)

    async def _execute_for_single_target(
        self, schedule: ScheduleInfo, task_meta: dict, bot
    ):
        """为单个目标（具体群组或全局）执行任务。"""
        plugin_name = schedule.plugin_name
        group_id = schedule.group_id

        try:
            is_blocked = await CommonUtils.task_is_block(bot, plugin_name, group_id)
            if is_blocked:
                target_desc = f"群 {group_id}" if group_id else "全局"
                logger.info(
                    f"插件 '{plugin_name}' 的定时任务在目标 [{target_desc}]"
                    "因功能被禁用而跳过执行。"
                )
                return

            task_func = task_meta["func"]
            job_kwargs = schedule.job_kwargs
            if not isinstance(job_kwargs, dict):
                logger.error(
                    f"任务 {schedule.id} 的 job_kwargs 不是字典类型: {type(job_kwargs)}"
                )
                return

            sig = inspect.signature(task_func)
            if "bot" in sig.parameters:
                job_kwargs["bot"] = bot

            logger.info(
                f"插件 '{plugin_name}' 开始为目标 [{group_id or '全局'}] "
                f"执行定时任务 (ID: {schedule.id})。"
            )
            task = asyncio.create_task(task_func(group_id, **job_kwargs))
            self._running_tasks.add(task)
            task.add_done_callback(self._running_tasks.discard)
            await task
        except Exception as e:
            logger.error(
                f"执行定时任务 (ID: {schedule.id}, 插件: {plugin_name}, "
                f"目标: {group_id or '全局'}) 时发生异常",
                e=e,
            )

    def _validate_and_prepare_kwargs(
        self, plugin_name: str, job_kwargs: dict | None
    ) -> tuple[bool, str | dict]:
        """验证并准备任务参数，应用默认值"""
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

    def _add_aps_job(self, schedule: ScheduleInfo):
        """根据 ScheduleInfo 对象添加或更新一个 APScheduler 任务。"""
        job_id = self._get_job_id(schedule.id)
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

        if not isinstance(schedule.trigger_config, dict):
            logger.error(
                f"任务 {schedule.id} 的 trigger_config 不是字典类型: "
                f"{type(schedule.trigger_config)}"
            )
            return

        scheduler.add_job(
            self._execute_job,
            trigger=schedule.trigger_type,
            id=job_id,
            misfire_grace_time=300,
            args=[schedule.id],
            **schedule.trigger_config,
        )
        logger.debug(
            f"已在 APScheduler 中添加/更新任务: {job_id} "
            f"with trigger: {schedule.trigger_config}"
        )

    def _remove_aps_job(self, schedule_id: int):
        """移除一个 APScheduler 任务。"""
        job_id = self._get_job_id(schedule_id)
        try:
            scheduler.remove_job(job_id)
            logger.debug(f"已从 APScheduler 中移除任务: {job_id}")
        except Exception:
            pass

    async def add_schedule(
        self,
        plugin_name: str,
        group_id: str | None,
        trigger_type: str,
        trigger_config: dict,
        job_kwargs: dict | None = None,
        bot_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        添加或更新一个定时任务。
        """
        if plugin_name not in self._registered_tasks:
            return False, f"插件 '{plugin_name}' 没有注册可用的定时任务。"

        is_valid, result = self._validate_and_prepare_kwargs(plugin_name, job_kwargs)
        if not is_valid:
            return False, str(result)

        validated_job_kwargs = result

        effective_bot_id = bot_id if group_id == "__ALL_GROUPS__" else None

        search_kwargs = {
            "plugin_name": plugin_name,
            "group_id": group_id,
        }
        if effective_bot_id:
            search_kwargs["bot_id"] = effective_bot_id
        else:
            search_kwargs["bot_id__isnull"] = True

        defaults = {
            "trigger_type": trigger_type,
            "trigger_config": trigger_config,
            "job_kwargs": validated_job_kwargs,
            "is_enabled": True,
        }

        schedule = await ScheduleInfo.filter(**search_kwargs).first()
        created = False

        if schedule:
            for key, value in defaults.items():
                setattr(schedule, key, value)
            await schedule.save()
        else:
            creation_kwargs = {
                "plugin_name": plugin_name,
                "group_id": group_id,
                "bot_id": effective_bot_id,
                **defaults,
            }
            schedule = await ScheduleInfo.create(**creation_kwargs)
            created = True
        self._add_aps_job(schedule)
        action = "设置" if created else "更新"
        return True, f"已成功{action}插件 '{plugin_name}' 的定时任务。"

    async def add_schedule_for_all(
        self,
        plugin_name: str,
        trigger_type: str,
        trigger_config: dict,
        job_kwargs: dict | None = None,
    ) -> tuple[int, int]:
        """为所有机器人所在的群组添加定时任务。"""
        if plugin_name not in self._registered_tasks:
            raise ValueError(f"插件 '{plugin_name}' 没有注册可用的定时任务。")

        groups = set()
        for bot in get_bots().values():
            try:
                group_list, _ = await PlatformUtils.get_group_list(bot)
                groups.update(
                    g.group_id for g in group_list if g.group_id and not g.channel_id
                )
            except Exception as e:
                logger.error(f"获取 Bot {bot.self_id} 的群列表失败", e=e)

        success_count = 0
        fail_count = 0
        for gid in groups:
            try:
                success, _ = await self.add_schedule(
                    plugin_name, gid, trigger_type, trigger_config, job_kwargs
                )
                if success:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"为群 {gid} 添加定时任务失败: {e}", e=e)
                fail_count += 1
            await asyncio.sleep(0.05)
        return success_count, fail_count

    async def update_schedule(
        self,
        schedule_id: int,
        trigger_type: str | None = None,
        trigger_config: dict | None = None,
        job_kwargs: dict | None = None,
    ) -> tuple[bool, str]:
        """部分更新一个已存在的定时任务。"""
        schedule = await self.get_schedule_by_id(schedule_id)
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
            if not isinstance(schedule.job_kwargs, dict):
                return False, f"任务 {schedule_id} 的 job_kwargs 数据格式错误。"

            merged_kwargs = schedule.job_kwargs.copy()
            merged_kwargs.update(job_kwargs)

            is_valid, result = self._validate_and_prepare_kwargs(
                schedule.plugin_name, merged_kwargs
            )
            if not is_valid:
                return False, str(result)

            schedule.job_kwargs = result  # type: ignore
            updated_fields.append("job_kwargs")

        if not updated_fields:
            return True, "没有任何需要更新的配置。"

        await schedule.save(update_fields=updated_fields)
        self._add_aps_job(schedule)
        return True, f"成功更新了任务 ID: {schedule_id} 的配置。"

    async def remove_schedule(
        self, plugin_name: str, group_id: str | None, bot_id: str | None = None
    ) -> tuple[bool, str]:
        """移除指定插件和群组的定时任务。"""
        query = {"plugin_name": plugin_name, "group_id": group_id}
        if bot_id:
            query["bot_id"] = bot_id

        schedules = await ScheduleInfo.filter(**query)
        if not schedules:
            msg = (
                f"未找到与 Bot {bot_id} 相关的群 {group_id} "
                f"的插件 '{plugin_name}' 定时任务。"
            )
            return (False, msg)

        for schedule in schedules:
            self._remove_aps_job(schedule.id)
            await schedule.delete()

        target_desc = f"群 {group_id}" if group_id else "全局"
        msg = (
            f"已取消 Bot {bot_id} 在 [{target_desc}] "
            f"的插件 '{plugin_name}' 所有定时任务。"
        )
        return (True, msg)

    async def remove_schedule_for_all(
        self, plugin_name: str, bot_id: str | None = None
    ) -> int:
        """移除指定插件在所有群组的定时任务。"""
        query = {"plugin_name": plugin_name}
        if bot_id:
            query["bot_id"] = bot_id

        schedules_to_delete = await ScheduleInfo.filter(**query).all()
        if not schedules_to_delete:
            return 0

        for schedule in schedules_to_delete:
            self._remove_aps_job(schedule.id)
            await schedule.delete()
            await asyncio.sleep(0.01)

        return len(schedules_to_delete)

    async def remove_schedules_by_group(self, group_id: str) -> tuple[bool, str]:
        """移除指定群组的所有定时任务。"""
        schedules = await ScheduleInfo.filter(group_id=group_id)
        if not schedules:
            return False, f"群 {group_id} 没有任何定时任务。"

        count = 0
        for schedule in schedules:
            self._remove_aps_job(schedule.id)
            await schedule.delete()
            count += 1
            await asyncio.sleep(0.01)

        return True, f"已成功移除群 {group_id} 的 {count} 个定时任务。"

    async def pause_schedules_by_group(self, group_id: str) -> tuple[int, str]:
        """暂停指定群组的所有定时任务。"""
        schedules = await ScheduleInfo.filter(group_id=group_id, is_enabled=True)
        if not schedules:
            return 0, f"群 {group_id} 没有正在运行的定时任务可暂停。"

        count = 0
        for schedule in schedules:
            success, _ = await self.pause_schedule(schedule.id)
            if success:
                count += 1
            await asyncio.sleep(0.01)

        return count, f"已成功暂停群 {group_id} 的 {count} 个定时任务。"

    async def resume_schedules_by_group(self, group_id: str) -> tuple[int, str]:
        """恢复指定群组的所有定时任务。"""
        schedules = await ScheduleInfo.filter(group_id=group_id, is_enabled=False)
        if not schedules:
            return 0, f"群 {group_id} 没有已暂停的定时任务可恢复。"

        count = 0
        for schedule in schedules:
            success, _ = await self.resume_schedule(schedule.id)
            if success:
                count += 1
            await asyncio.sleep(0.01)

        return count, f"已成功恢复群 {group_id} 的 {count} 个定时任务。"

    async def pause_schedules_by_plugin(self, plugin_name: str) -> tuple[int, str]:
        """暂停指定插件在所有群组的定时任务。"""
        schedules = await ScheduleInfo.filter(plugin_name=plugin_name, is_enabled=True)
        if not schedules:
            return 0, f"插件 '{plugin_name}' 没有正在运行的定时任务可暂停。"

        count = 0
        for schedule in schedules:
            success, _ = await self.pause_schedule(schedule.id)
            if success:
                count += 1
            await asyncio.sleep(0.01)

        return (
            count,
            f"已成功暂停插件 '{plugin_name}' 在所有群组的 {count} 个定时任务。",
        )

    async def resume_schedules_by_plugin(self, plugin_name: str) -> tuple[int, str]:
        """恢复指定插件在所有群组的定时任务。"""
        schedules = await ScheduleInfo.filter(plugin_name=plugin_name, is_enabled=False)
        if not schedules:
            return 0, f"插件 '{plugin_name}' 没有已暂停的定时任务可恢复。"

        count = 0
        for schedule in schedules:
            success, _ = await self.resume_schedule(schedule.id)
            if success:
                count += 1
            await asyncio.sleep(0.01)

        return (
            count,
            f"已成功恢复插件 '{plugin_name}' 在所有群组的 {count} 个定时任务。",
        )

    async def pause_schedule_by_plugin_group(
        self, plugin_name: str, group_id: str | None, bot_id: str | None = None
    ) -> tuple[bool, str]:
        """暂停指定插件在指定群组的定时任务。"""
        query = {"plugin_name": plugin_name, "group_id": group_id, "is_enabled": True}
        if bot_id:
            query["bot_id"] = bot_id

        schedules = await ScheduleInfo.filter(**query)
        if not schedules:
            return (
                False,
                f"群 {group_id} 未设置插件 '{plugin_name}' 的定时任务或任务已暂停。",
            )

        count = 0
        for schedule in schedules:
            success, _ = await self.pause_schedule(schedule.id)
            if success:
                count += 1

        return (
            True,
            f"已成功暂停群 {group_id} 的插件 '{plugin_name}' 共 {count} 个定时任务。",
        )

    async def resume_schedule_by_plugin_group(
        self, plugin_name: str, group_id: str | None, bot_id: str | None = None
    ) -> tuple[bool, str]:
        """恢复指定插件在指定群组的定时任务。"""
        query = {"plugin_name": plugin_name, "group_id": group_id, "is_enabled": False}
        if bot_id:
            query["bot_id"] = bot_id

        schedules = await ScheduleInfo.filter(**query)
        if not schedules:
            return (
                False,
                f"群 {group_id} 未设置插件 '{plugin_name}' 的定时任务或任务已启用。",
            )

        count = 0
        for schedule in schedules:
            success, _ = await self.resume_schedule(schedule.id)
            if success:
                count += 1

        return (
            True,
            f"已成功恢复群 {group_id} 的插件 '{plugin_name}' 共 {count} 个定时任务。",
        )

    async def remove_all_schedules(self) -> tuple[int, str]:
        """移除所有群组的所有定时任务。"""
        schedules = await ScheduleInfo.all()
        if not schedules:
            return 0, "当前没有任何定时任务。"

        count = 0
        for schedule in schedules:
            self._remove_aps_job(schedule.id)
            await schedule.delete()
            count += 1
            await asyncio.sleep(0.01)

        return count, f"已成功移除所有群组的 {count} 个定时任务。"

    async def pause_all_schedules(self) -> tuple[int, str]:
        """暂停所有群组的所有定时任务。"""
        schedules = await ScheduleInfo.filter(is_enabled=True)
        if not schedules:
            return 0, "当前没有正在运行的定时任务可暂停。"

        count = 0
        for schedule in schedules:
            success, _ = await self.pause_schedule(schedule.id)
            if success:
                count += 1
            await asyncio.sleep(0.01)

        return count, f"已成功暂停所有群组的 {count} 个定时任务。"

    async def resume_all_schedules(self) -> tuple[int, str]:
        """恢复所有群组的所有定时任务。"""
        schedules = await ScheduleInfo.filter(is_enabled=False)
        if not schedules:
            return 0, "当前没有已暂停的定时任务可恢复。"

        count = 0
        for schedule in schedules:
            success, _ = await self.resume_schedule(schedule.id)
            if success:
                count += 1
            await asyncio.sleep(0.01)

        return count, f"已成功恢复所有群组的 {count} 个定时任务。"

    async def remove_schedule_by_id(self, schedule_id: int) -> tuple[bool, str]:
        """通过ID移除指定的定时任务。"""
        schedule = await self.get_schedule_by_id(schedule_id)
        if not schedule:
            return False, f"未找到 ID 为 {schedule_id} 的定时任务。"

        self._remove_aps_job(schedule.id)
        await schedule.delete()

        return (
            True,
            f"已删除插件 '{schedule.plugin_name}' 在群 {schedule.group_id} "
            f"的定时任务 (ID: {schedule.id})。",
        )

    async def get_schedule_by_id(self, schedule_id: int) -> ScheduleInfo | None:
        """通过ID获取定时任务信息。"""
        return await ScheduleInfo.get_or_none(id=schedule_id)

    async def get_schedules(
        self, plugin_name: str, group_id: str | None
    ) -> list[ScheduleInfo]:
        """获取特定群组特定插件的所有定时任务。"""
        return await ScheduleInfo.filter(plugin_name=plugin_name, group_id=group_id)

    async def get_schedule(
        self, plugin_name: str, group_id: str | None
    ) -> ScheduleInfo | None:
        """获取特定群组的定时任务信息。"""
        return await ScheduleInfo.get_or_none(
            plugin_name=plugin_name, group_id=group_id
        )

    async def get_all_schedules(
        self, plugin_name: str | None = None
    ) -> list[ScheduleInfo]:
        """获取所有定时任务信息，可按插件名过滤。"""
        if plugin_name:
            return await ScheduleInfo.filter(plugin_name=plugin_name).all()
        return await ScheduleInfo.all()

    async def get_schedule_status(self, schedule_id: int) -> dict | None:
        """获取任务的详细状态。"""
        schedule = await self.get_schedule_by_id(schedule_id)
        if not schedule:
            return None

        job_id = self._get_job_id(schedule.id)
        job = scheduler.get_job(job_id)

        status = {
            "id": schedule.id,
            "bot_id": schedule.bot_id,
            "plugin_name": schedule.plugin_name,
            "group_id": schedule.group_id,
            "is_enabled": schedule.is_enabled,
            "trigger_type": schedule.trigger_type,
            "trigger_config": schedule.trigger_config,
            "job_kwargs": schedule.job_kwargs,
            "next_run_time": job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
            if job and job.next_run_time
            else "N/A",
            "is_paused_in_scheduler": not bool(job.next_run_time) if job else "N/A",
        }
        return status

    async def pause_schedule(self, schedule_id: int) -> tuple[bool, str]:
        """暂停一个定时任务。"""
        schedule = await self.get_schedule_by_id(schedule_id)
        if not schedule or not schedule.is_enabled:
            return False, "任务不存在或已暂停。"

        schedule.is_enabled = False
        await schedule.save(update_fields=["is_enabled"])

        job_id = self._get_job_id(schedule.id)
        try:
            scheduler.pause_job(job_id)
        except Exception:
            pass

        return (
            True,
            f"已暂停插件 '{schedule.plugin_name}' 在群 {schedule.group_id} "
            f"的定时任务 (ID: {schedule.id})。",
        )

    async def resume_schedule(self, schedule_id: int) -> tuple[bool, str]:
        """恢复一个定时任务。"""
        schedule = await self.get_schedule_by_id(schedule_id)
        if not schedule or schedule.is_enabled:
            return False, "任务不存在或已启用。"

        schedule.is_enabled = True
        await schedule.save(update_fields=["is_enabled"])

        job_id = self._get_job_id(schedule.id)
        try:
            scheduler.resume_job(job_id)
        except Exception:
            self._add_aps_job(schedule)

        return (
            True,
            f"已恢复插件 '{schedule.plugin_name}' 在群 {schedule.group_id} "
            f"的定时任务 (ID: {schedule.id})。",
        )

    async def trigger_now(self, schedule_id: int) -> tuple[bool, str]:
        """手动触发一个定时任务。"""
        schedule = await self.get_schedule_by_id(schedule_id)
        if not schedule:
            return False, f"未找到 ID 为 {schedule_id} 的定时任务。"

        if schedule.plugin_name not in self._registered_tasks:
            return False, f"插件 '{schedule.plugin_name}' 没有注册可用的定时任务。"

        try:
            await self._execute_job(schedule.id)
            return (
                True,
                f"已手动触发插件 '{schedule.plugin_name}' 在群 {schedule.group_id} "
                f"的定时任务 (ID: {schedule.id})。",
            )
        except Exception as e:
            logger.error(f"手动触发任务失败: {e}")
            return False, f"手动触发任务失败: {e}"


scheduler_manager = SchedulerManager()


@PriorityLifecycle.on_startup(priority=90)
async def _load_schedules_from_db():
    """在服务启动时从数据库加载并调度所有任务。"""
    Config.add_plugin_config(
        "SchedulerManager",
        SCHEDULE_CONCURRENCY_KEY,
        5,
        help="“所有群组”类型定时任务的并发执行数量限制",
        type=int,
    )

    logger.info("正在从数据库加载并调度所有定时任务...")
    schedules = await ScheduleInfo.filter(is_enabled=True).all()
    count = 0
    for schedule in schedules:
        if schedule.plugin_name in scheduler_manager._registered_tasks:
            scheduler_manager._add_aps_job(schedule)
            count += 1
        else:
            logger.warning(f"跳过加载定时任务：插件 '{schedule.plugin_name}' 未注册。")
    logger.info(f"定时任务加载完成，共成功加载 {count} 个任务。")

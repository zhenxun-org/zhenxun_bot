"""
引擎适配层 (Adapter) 与 任务执行逻辑 (Job)

封装所有对具体调度器引擎 (APScheduler) 的操作，
以及被 APScheduler 实际调度的函数。
"""

import asyncio
from collections.abc import Callable
from functools import partial
import random
import time
from typing import cast

from apscheduler.jobstores.base import JobLookupError
from nonebot.adapters import Bot
from nonebot.dependencies import Dependent
from nonebot.exception import FinishedException, PausedException, SkippedException
from nonebot.matcher import Matcher
from nonebot.typing import T_State
from nonebot_plugin_apscheduler import scheduler
from pydantic import BaseModel

from zhenxun.configs.config import Config
from zhenxun.models.scheduled_job import ScheduledJob
from zhenxun.services.log import logger
from zhenxun.services.message_load import should_pause_tasks
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.decorator.retry import Retry
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.pydantic_compat import parse_as

from .registry import scheduler_registry
from .repository import ScheduleRepository
from .types import ExecutionPolicy, ScheduleContext, TargetType

JOB_PREFIX = "zhenxun_schedule_"
SCHEDULE_CONCURRENCY_KEY = "all_groups_concurrency_limit"
_LAST_PRESSURE_SKIP = 0.0


class APSchedulerAdapter:
    """封装对 APScheduler 的操作"""

    @staticmethod
    def _get_job_id(schedule_id: int) -> str:
        """
        生成 APScheduler 的 Job ID

        参数:
            schedule_id: 定时任务的ID。

        返回:
            str: APScheduler 使用的 Job ID。
        """
        return f"{JOB_PREFIX}{schedule_id}"

    @staticmethod
    def add_or_reschedule_job(schedule: ScheduledJob):
        """
        根据 ScheduledJob 添加或重新调度一个 APScheduler 任务

        参数:
            schedule: 定时任务对象，包含任务的所有配置信息。
        """
        job_id = APSchedulerAdapter._get_job_id(schedule.id)

        try:
            scheduler.remove_job(job_id)
        except JobLookupError:
            pass
        except Exception as e:
            logger.debug(f"尝试覆盖 APScheduler 任务 {job_id} 时发生异常: {e}")

        if not isinstance(schedule.trigger_config, dict):
            logger.error(
                f"任务 {schedule.id} 的 trigger_config 不是字典类型: "
                f"{type(schedule.trigger_config)}"
            )
            return

        trigger_params = schedule.trigger_config.copy()
        execution_options = (
            schedule.execution_options
            if isinstance(schedule.execution_options, dict)
            else {}
        )
        if jitter := execution_options.get("jitter"):
            if isinstance(jitter, int) and jitter > 0:
                trigger_params["jitter"] = jitter

        concurrency_policy = execution_options.get("concurrency_policy", "ALLOW")
        job_params = {
            "id": job_id,
            "misfire_grace_time": 300,
            "args": [schedule.id],
        }

        if concurrency_policy == "SKIP":
            job_params["max_instances"] = 1
            job_params["coalesce"] = True
        elif concurrency_policy == "QUEUE":
            job_params["max_instances"] = 1
            job_params["coalesce"] = False

        scheduler.add_job(
            _execute_persistent_job,
            trigger=schedule.trigger_type,
            **job_params,
            **trigger_params,
        )
        logger.debug(
            f"已添加或更新APScheduler任务: {job_id} | 并发策略: {concurrency_policy}, "
            f"抖动: {trigger_params.get('jitter', '无')}"
        )

    @staticmethod
    def remove_job(schedule_id: int):
        """
        移除一个 APScheduler 任务

        参数:
            schedule_id: 要移除的定时任务ID。
        """
        job_id = APSchedulerAdapter._get_job_id(schedule_id)
        try:
            scheduler.remove_job(job_id)
            logger.debug(f"已从APScheduler中移除任务: {job_id}")
        except JobLookupError:
            logger.debug(f"APScheduler 中未找到任务: {job_id}，无需移除。")
        except Exception as e:
            logger.debug(f"从 APScheduler 移除任务 {job_id} 时发生异常: {e}")

    @staticmethod
    def pause_job(schedule_id: int):
        """
        暂停一个 APScheduler 任务

        参数:
            schedule_id: 要暂停的定时任务ID。
        """
        job_id = APSchedulerAdapter._get_job_id(schedule_id)
        try:
            scheduler.pause_job(job_id)
        except JobLookupError:
            logger.debug(f"APScheduler 中未找到任务: {job_id}，无法暂停。")
        except Exception as e:
            logger.debug(f"暂停 APScheduler 任务 {job_id} 时发生异常: {e}")

    @staticmethod
    def resume_job(schedule_id: int):
        """
        恢复一个 APScheduler 任务

        参数:
            schedule_id: 要恢复的定时任务ID。
        """
        job_id = APSchedulerAdapter._get_job_id(schedule_id)
        try:
            scheduler.resume_job(job_id)
        except JobLookupError:
            logger.debug(f"APScheduler 中未找到任务: {job_id}，尝试重新注册...")

            async def _re_add_job():
                schedule = await ScheduleRepository.get_by_id(schedule_id)
                if schedule:
                    APSchedulerAdapter.add_or_reschedule_job(schedule)

            asyncio.create_task(_re_add_job())  # noqa: RUF006
        except Exception as e:
            logger.debug(f"恢复 APScheduler 任务 {job_id} 时发生异常: {e}")

    @staticmethod
    def get_job_status(schedule_id: int) -> dict:
        """
        获取 APScheduler Job 的状态

        参数:
            schedule_id: 定时任务的ID。

        返回:
            dict: 包含任务状态信息的字典，包含next_run_time等字段。
        """
        job_id = APSchedulerAdapter._get_job_id(schedule_id)
        job = scheduler.get_job(job_id)
        return {
            "next_run_time": job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
            if job and job.next_run_time
            else "N/A",
            "is_paused_in_scheduler": not bool(job.next_run_time) if job else "N/A",
        }

    @staticmethod
    def add_ephemeral_job(
        job_id: str,
        func: Callable,
        trigger_type: str,
        trigger_config: dict,
        context: ScheduleContext,
    ):
        """
        直接向 APScheduler 添加一个临时的、非持久化的任务

        参数:
            job_id: 临时任务的唯一ID。
            func: 要执行的函数。
            trigger_type: 触发器类型。
            trigger_config: 触发器配置字典。
            context: 任务执行上下文。
        """
        job = scheduler.get_job(job_id)
        if job:
            logger.warning(f"尝试添加一个已存在的临时任务ID: {job_id}，操作被忽略。")
            return

        scheduler.add_job(
            _execute_ephemeral_job,
            trigger=trigger_type,
            id=job_id,
            misfire_grace_time=60,
            kwargs={"context": context},
            **trigger_config,
        )
        logger.debug(f"已添加新的临时APScheduler任务: {job_id}")


async def _run_dependent_task(
    func: Callable, injected_params: dict, bot: Bot, state: T_State
):
    """将普通函数包装为 NoneBot Dependent 并执行的内部辅助方法"""

    async def wrapper(bot: Bot):
        return await func(bot=bot, **injected_params)

    dependent = Dependent.parse(call=wrapper, allow_types=Matcher.HANDLER_PARAM_TYPES)
    return await dependent(bot=bot, state=state)


async def _execute_single_job_instance(
    schedule: ScheduledJob, bot, target_id: str | None = None
):
    """
    负责执行一个具体目标的任务实例。
    """

    plugin_name = schedule.plugin_name

    actual_group_id = None
    actual_user_id = None

    if schedule.target_type in (
        TargetType.GROUP,
        TargetType.TAG,
        TargetType.ALL_GROUPS,
    ):
        actual_group_id = target_id or (
            schedule.target_identifier
            if schedule.target_type == TargetType.GROUP
            else None
        )
    elif schedule.target_type == TargetType.USER:
        actual_user_id = target_id or schedule.target_identifier

    target_log = actual_group_id or actual_user_id

    task_meta = scheduler_registry.tasks.get(plugin_name)

    if not task_meta:
        logger.error(f"无法执行任务：插件 '{plugin_name}' 在执行期间变得不可用。")
        return

    is_blocked = await CommonUtils.task_is_block(bot, plugin_name, actual_group_id)
    if is_blocked:
        target_desc = (
            f"群 {actual_group_id}"
            if actual_group_id
            else (f"用户 {actual_user_id}" if actual_user_id else "全局")
        )
        logger.info(
            f"插件 '{plugin_name}' 的定时任务在目标 [{target_desc}] "
            f"因功能被禁用而跳过执行。"
        )
        return

    context = ScheduleContext(
        schedule_id=schedule.id,
        plugin_name=plugin_name,
        bot_id=bot.self_id,
        platform_scope=PlatformUtils.get_platform_scope(bot),
        group_id=actual_group_id,
        user_id=actual_user_id,
        job_kwargs=schedule.job_kwargs if isinstance(schedule.job_kwargs, dict) else {},
    )
    state: T_State = {ScheduleContext: context}

    policy_data = context.job_kwargs.pop("execution_policy", {})
    policy = ExecutionPolicy(**policy_data)

    async def task_execution_coro():
        injected_params = {"context": context}

        params_model = task_meta.get("model")
        if params_model and isinstance(context.job_kwargs, dict):
            try:
                if isinstance(params_model, type) and issubclass(
                    params_model, BaseModel
                ):
                    params_instance = parse_as(params_model, context.job_kwargs)
                    injected_params["params"] = params_instance  # type: ignore
            except Exception as e:
                logger.error(
                    f"任务 {schedule.id} (目标: {target_log}) 参数验证失败: {e}",
                    e=e,
                )
                raise

        func = cast(Callable, task_meta["func"])
        return await _run_dependent_task(func, injected_params, bot, state)

    try:
        if policy.retries > 0:
            on_success_handler = None
            if policy.on_success_callback:
                on_success_handler = partial(policy.on_success_callback, context)

            on_failure_handler = None
            if policy.on_failure_callback:
                on_failure_handler = partial(policy.on_failure_callback, context)

            retry_exceptions = tuple(policy.retry_on_exceptions or [])

            retry_decorator = Retry.api(
                stop_max_attempt=policy.retries + 1,
                strategy="exponential" if policy.retry_backoff else "fixed",
                wait_fixed_seconds=policy.retry_delay_seconds,
                exception=retry_exceptions,
                on_success=on_success_handler,
                on_failure=on_failure_handler,
                log_name=f"ScheduledJob-{schedule.id}-{target_log or 'global'}",
            )

            decorated_executor = retry_decorator(task_execution_coro)
            await decorated_executor()
        else:
            logger.info(
                f"插件 '{plugin_name}' 开始为目标 [{target_log or '全局'}] "
                f"执行定时任务 (ID: {schedule.id})。"
            )
            await task_execution_coro()

    except (PausedException, FinishedException, SkippedException) as e:
        logger.warning(
            f"定时任务 {schedule.id} (目标: {target_log}) 被中断: {type(e).__name__}"
        )
    except Exception as e:
        logger.error(
            f"执行定时任务 {schedule.id} (目标: {target_log}) "
            f"时发生未被策略处理的最终错误",
            e=e,
        )


class ExecutionDispatcher:
    """执行管线分发器：处理并发限制、串行间隔、随机散列打散逻辑"""

    @staticmethod
    async def dispatch(
        schedule: ScheduledJob, bot: Bot, resolved_targets: list[str | None]
    ):
        concurrency_limit = Config.get_config(
            "SchedulerManager", SCHEDULE_CONCURRENCY_KEY, 5
        )
        semaphore = asyncio.Semaphore(concurrency_limit if concurrency_limit > 0 else 5)

        spread_config = (
            schedule.execution_options
            if isinstance(schedule.execution_options, dict)
            else {}
        )
        interval_seconds = spread_config.get("interval")

        if interval_seconds is not None and interval_seconds > 0:
            logger.debug(
                f"任务 {schedule.id}: 使用串行模式执行 {len(resolved_targets)} "
                f"个目标，固定间隔 {interval_seconds} 秒。"
            )
            for i, target_id in enumerate(resolved_targets):
                if i > 0:
                    await asyncio.sleep(interval_seconds)
                await _execute_single_job_instance(schedule, bot, target_id=target_id)
        else:
            spread_seconds = spread_config.get("spread", 1.0)
            logger.debug(
                f"任务 {schedule.id}: 将在 {spread_seconds:.2f} 秒内分散执行 "
                f"{len(resolved_targets)} 个目标。"
            )

            async def worker(target_id: str | None):
                delay = random.uniform(0.1, spread_seconds)
                await asyncio.sleep(delay)
                async with semaphore:
                    await _execute_single_job_instance(
                        schedule, bot, target_id=target_id
                    )

            tasks_to_run = [worker(target_id) for target_id in resolved_targets]
            if tasks_to_run:
                await asyncio.gather(*tasks_to_run, return_exceptions=True)


async def _execute_ephemeral_job(context: ScheduleContext):
    """
    执行临时的、内存态的定时任务 (Ephemeral Job)
    """
    plugin_name = context.plugin_name
    task_meta = scheduler_registry.tasks.get(plugin_name)
    if not task_meta or not task_meta["func"]:
        logger.error(f"无法执行临时任务：函数 '{plugin_name}' 未注册。")
        return

    try:
        bot = PlatformUtils.resolve_bot(
            bot_id=context.bot_id,
            log_cmd=f"临时任务 {plugin_name}",
        )
        if bot is None:
            return

        context.platform_scope = PlatformUtils.get_platform_scope(bot)
        logger.info(f"开始执行临时任务: {plugin_name}")
        injected_params = {"context": context}
        state: T_State = {ScheduleContext: context}

        func = cast(Callable, task_meta["func"])
        await _run_dependent_task(func, injected_params, bot, state)
        logger.info(f"临时任务 '{plugin_name}' 执行完成。")
    except Exception as e:
        logger.error(f"执行临时任务 '{plugin_name}' 时发生错误", e=e)


async def _execute_persistent_job(schedule_id: int, force: bool = False):
    """
    执行数据库中持久化的定时任务 (Persistent Job)
    """
    schedule = None

    global _LAST_PRESSURE_SKIP
    if should_pause_tasks():
        now = time.time()
        if now - _LAST_PRESSURE_SKIP > 30:
            _LAST_PRESSURE_SKIP = now
            logger.info("scheduler paused due to message pressure")
        return

    scheduler_registry.running_tasks.add(schedule_id)
    try:
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule or (not schedule.is_enabled and not force):
            logger.warning(f"定时任务 {schedule_id} 不存在或已禁用，跳过执行。")
            return

        bot = PlatformUtils.resolve_bot(
            bot_id=schedule.bot_id, log_cmd=f"任务 {schedule_id}"
        )
        if bot is None:
            return

        resolver = scheduler_registry.target_resolvers.get(schedule.target_type)
        if not resolver:
            logger.error(
                f"任务 {schedule.id} 的目标类型 '{schedule.target_type}' "
                f"没有注册解析器，执行跳过。"
            )
            raise ValueError(f"未知的目标类型: {schedule.target_type}")

        try:
            resolved_targets = await resolver(schedule.target_identifier, bot)
        except Exception as e:
            logger.error(f"为任务 {schedule.id} 解析目标失败", e=e)
            raise

        logger.info(
            f"任务 {schedule.id} ({schedule.name or schedule.plugin_name}) 开始执行, "
            f"目标类型: {schedule.target_type}, "
            f"解析出 {len(resolved_targets)} 个目标"
        )

        await ExecutionDispatcher.dispatch(schedule, bot, resolved_targets)

        await ScheduleRepository.update_run_status(schedule, is_success=True)

        if schedule.is_one_off:
            logger.info(f"一次性任务 {schedule.id} 执行成功，将被删除。")
            await ScheduledJob.filter(id=schedule.id).delete()
            APSchedulerAdapter.remove_job(schedule.id)
            if schedule.plugin_name.startswith("runtime_one_off__"):
                scheduler_registry.tasks.pop(schedule.plugin_name, None)
                logger.debug(f"已注销一次性运行时任务: {schedule.plugin_name}")

    except Exception as e:
        logger.error(f"执行任务 {schedule_id} 期间发生严重错误", e=e)

        if schedule:
            await ScheduleRepository.update_run_status(schedule, is_success=False)

    finally:
        if schedule_id is not None:
            scheduler_registry.running_tasks.discard(schedule_id)

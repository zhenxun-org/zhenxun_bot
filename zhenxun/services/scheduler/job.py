"""
定时任务的执行逻辑

包含被 APScheduler 实际调度的函数，以及处理不同目标（单个、所有群组）的执行策略。
"""

import asyncio
import copy
import inspect
import random

import nonebot

from zhenxun.configs.config import Config
from zhenxun.models.schedule_info import ScheduleInfo
from zhenxun.services.log import logger
from zhenxun.utils.common_utils import CommonUtils
from zhenxun.utils.decorator.retry import Retry
from zhenxun.utils.platform import PlatformUtils

SCHEDULE_CONCURRENCY_KEY = "all_groups_concurrency_limit"


async def _execute_job(schedule_id: int):
    """
    APScheduler 调度的入口函数。
    根据 schedule_id 处理特定任务、所有群组任务或全局任务。
    """
    from .repository import ScheduleRepository
    from .service import scheduler_manager

    scheduler_manager._running_tasks.add(schedule_id)
    try:
        schedule = await ScheduleRepository.get_by_id(schedule_id)
        if not schedule or not schedule.is_enabled:
            logger.warning(f"定时任务 {schedule_id} 不存在或已禁用，跳过执行。")
            return

        plugin_name = schedule.plugin_name

        task_meta = scheduler_manager._registered_tasks.get(plugin_name)
        if not task_meta:
            logger.error(
                f"无法执行定时任务：插件 '{plugin_name}' 未注册或已卸载。将禁用该任务。"
            )
            schedule.is_enabled = False
            await ScheduleRepository.save(schedule, update_fields=["is_enabled"])
            from .adapter import APSchedulerAdapter

            APSchedulerAdapter.remove_job(schedule.id)
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

        if schedule.group_id == scheduler_manager.ALL_GROUPS:
            await _execute_for_all_groups(schedule, task_meta, bot)
        else:
            await _execute_for_single_target(schedule, task_meta, bot)
    finally:
        scheduler_manager._running_tasks.discard(schedule_id)


async def _execute_for_all_groups(schedule: ScheduleInfo, task_meta: dict, bot):
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
        await asyncio.sleep(random.uniform(0, 59))
        async with semaphore:
            temp_schedule = copy.deepcopy(schedule)
            temp_schedule.group_id = gid
            await _execute_for_single_target(temp_schedule, task_meta, bot)
            await asyncio.sleep(random.uniform(0.1, 0.5))

    tasks_to_run = []
    for gid in all_gids:
        if gid in specific_tasks_gids:
            logger.debug(f"群组 {gid} 已有特定任务，跳过 'all' 任务的执行。")
            continue
        tasks_to_run.append(worker(gid))

    if tasks_to_run:
        await asyncio.gather(*tasks_to_run)


async def _execute_for_single_target(schedule: ScheduleInfo, task_meta: dict, bot):
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

        max_retries = Config.get_config("SchedulerManager", "JOB_MAX_RETRIES", 2)
        retry_delay = Config.get_config("SchedulerManager", "JOB_RETRY_DELAY", 10)

        @Retry.simple(
            stop_max_attempt=max_retries + 1,
            wait_fixed_seconds=retry_delay,
            log_name=f"定时任务执行:{schedule.plugin_name}",
        )
        async def _execute_task_with_retry():
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

            await task_func(group_id, **job_kwargs)

        try:
            logger.info(
                f"插件 '{schedule.plugin_name}' 开始为目标 "
                f"[{schedule.group_id or '全局'}] 执行定时任务 (ID: {schedule.id})。"
            )
            await _execute_task_with_retry()
        except Exception as e:
            logger.error(
                f"执行定时任务 (ID: {schedule.id}, 插件: {schedule.plugin_name}, "
                f"目标: {schedule.group_id or '全局'}) 在所有重试后最终失败",
                e=e,
            )
    except Exception as e:
        logger.error(
            f"执行定时任务 (ID: {schedule.id}, 插件: {plugin_name}, "
            f"目标: {group_id or '全局'}) 时发生异常",
            e=e,
        )

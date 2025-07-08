"""
引擎适配层 (Adapter)

封装所有对具体调度器引擎 (APScheduler) 的操作，
使上层服务与调度器实现解耦。
"""

from nonebot_plugin_apscheduler import scheduler

from zhenxun.models.schedule_info import ScheduleInfo
from zhenxun.services.log import logger

from .job import _execute_job

JOB_PREFIX = "zhenxun_schedule_"


class APSchedulerAdapter:
    """封装对 APScheduler 的操作"""

    @staticmethod
    def _get_job_id(schedule_id: int) -> str:
        """生成 APScheduler 的 Job ID"""
        return f"{JOB_PREFIX}{schedule_id}"

    @staticmethod
    def add_or_reschedule_job(schedule: ScheduleInfo):
        """根据 ScheduleInfo 添加或重新调度一个 APScheduler 任务"""
        job_id = APSchedulerAdapter._get_job_id(schedule.id)

        if not isinstance(schedule.trigger_config, dict):
            logger.error(
                f"任务 {schedule.id} 的 trigger_config 不是字典类型: "
                f"{type(schedule.trigger_config)}"
            )
            return

        job = scheduler.get_job(job_id)
        if job:
            scheduler.reschedule_job(
                job_id, trigger=schedule.trigger_type, **schedule.trigger_config
            )
            logger.debug(f"已更新APScheduler任务: {job_id}")
        else:
            scheduler.add_job(
                _execute_job,
                trigger=schedule.trigger_type,
                id=job_id,
                misfire_grace_time=300,
                args=[schedule.id],
                **schedule.trigger_config,
            )
            logger.debug(f"已添加新的APScheduler任务: {job_id}")

    @staticmethod
    def remove_job(schedule_id: int):
        """移除一个 APScheduler 任务"""
        job_id = APSchedulerAdapter._get_job_id(schedule_id)
        try:
            scheduler.remove_job(job_id)
            logger.debug(f"已从APScheduler中移除任务: {job_id}")
        except Exception:
            pass

    @staticmethod
    def pause_job(schedule_id: int):
        """暂停一个 APScheduler 任务"""
        job_id = APSchedulerAdapter._get_job_id(schedule_id)
        try:
            scheduler.pause_job(job_id)
        except Exception:
            pass

    @staticmethod
    def resume_job(schedule_id: int):
        """恢复一个 APScheduler 任务"""
        job_id = APSchedulerAdapter._get_job_id(schedule_id)
        try:
            scheduler.resume_job(job_id)
        except Exception:
            import asyncio

            from .repository import ScheduleRepository

            async def _re_add_job():
                schedule = await ScheduleRepository.get_by_id(schedule_id)
                if schedule:
                    APSchedulerAdapter.add_or_reschedule_job(schedule)

            asyncio.create_task(_re_add_job())  # noqa: RUF006

    @staticmethod
    def get_job_status(schedule_id: int) -> dict:
        """获取 APScheduler Job 的状态"""
        job_id = APSchedulerAdapter._get_job_id(schedule_id)
        job = scheduler.get_job(job_id)
        return {
            "next_run_time": job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
            if job and job.next_run_time
            else "N/A",
            "is_paused_in_scheduler": not bool(job.next_run_time) if job else "N/A",
        }

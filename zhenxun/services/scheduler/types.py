"""
定时任务服务的数据模型与类型定义
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class BaseTrigger(BaseModel):
    """触发器配置的基类"""

    trigger_type: str = Field(..., exclude=True)


class CronTrigger(BaseTrigger):
    """Cron 触发器配置"""

    trigger_type: Literal["cron"] = "cron"  # type: ignore
    year: int | str | None = None
    month: int | str | None = None
    day: int | str | None = None
    week: int | str | None = None
    day_of_week: int | str | None = None
    hour: int | str | None = None
    minute: int | str | None = None
    second: int | str | None = None
    start_date: datetime | str | None = None
    end_date: datetime | str | None = None
    timezone: str | None = None
    jitter: int | None = None


class IntervalTrigger(BaseTrigger):
    """Interval 触发器配置"""

    trigger_type: Literal["interval"] = "interval"  # type: ignore
    weeks: int = 0
    days: int = 0
    hours: int = 0
    minutes: int = 0
    seconds: int = 0
    start_date: datetime | str | None = None
    end_date: datetime | str | None = None
    timezone: str | None = None
    jitter: int | None = None


class DateTrigger(BaseTrigger):
    """Date 触发器配置"""

    trigger_type: Literal["date"] = "date"  # type: ignore
    run_date: datetime | str
    timezone: str | None = None


class Trigger:
    """
    一个用于创建类型安全触发器配置的工厂类。
    提供了流畅的、具备IDE自动补全功能的API。
    """

    @staticmethod
    def cron(**kwargs) -> CronTrigger:
        """创建一个 Cron 触发器配置。"""
        return CronTrigger(**kwargs)

    @staticmethod
    def interval(**kwargs) -> IntervalTrigger:
        """创建一个 Interval 触发器配置。"""
        return IntervalTrigger(**kwargs)

    @staticmethod
    def date(**kwargs) -> DateTrigger:
        """创建一个 Date 触发器配置。"""
        return DateTrigger(**kwargs)


class ExecutionOptions(BaseModel):
    """
    封装定时任务的执行策略，包括重试和回调。
    """

    jitter: int | None = Field(None, description="触发时间抖动(秒)")
    spread: int | None = Field(
        None, description="(并发模式)多目标执行的最大分散延迟(秒)"
    )
    interval: int | None = Field(
        None, description="多目标执行的固定间隔(秒)，设置后将强制串行执行"
    )
    concurrency_policy: Literal["ALLOW", "SKIP", "QUEUE"] = Field(
        "ALLOW", description="并发策略"
    )
    retries: int = 0
    retry_delay_seconds: int = 30


class ScheduleContext(BaseModel):
    """
    定时任务执行上下文，可通过依赖注入获取。
    """

    schedule_id: int = Field(..., description="数据库中的任务ID")
    plugin_name: str = Field(..., description="任务所属的插件名称")
    bot_id: str | None = Field(None, description="执行任务的Bot ID")
    group_id: str | None = Field(None, description="当前执行实例的目标群组ID")
    job_kwargs: dict = Field(default_factory=dict, description="任务配置的参数")


class ExecutionPolicy(BaseModel):
    """
    封装定时任务的执行策略，包括重试和回调。
    """

    retries: int = 0
    retry_delay_seconds: int = 30
    retry_backoff: bool = False
    retry_on_exceptions: list[type[Exception]] | None = None
    on_success_callback: Callable[[ScheduleContext, Any], Awaitable[None]] | None = None
    on_failure_callback: (
        Callable[[ScheduleContext, Exception], Awaitable[None]] | None
    ) = None

    class Config:
        arbitrary_types_allowed = True


class ScheduledJobDeclaration(BaseModel):
    """用于在启动时声明默认定时任务的内部数据模型"""

    plugin_name: str
    group_id: str | None
    bot_id: str | None
    trigger: BaseTrigger
    job_kwargs: dict[str, Any]

    class Config:
        arbitrary_types_allowed = True


class EphemeralJobDeclaration(BaseModel):
    """用于在启动时声明临时任务的内部数据模型"""

    plugin_name: str
    func: Callable[..., Awaitable[Any]]
    trigger: BaseTrigger

    class Config:
        arbitrary_types_allowed = True

"""
定时任务服务的数据模型与类型定义
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from zhenxun.utils.pydantic_compat import model_validate


class TargetType(str, Enum):
    """定时任务执行目标类型枚举"""

    GLOBAL = "GLOBAL"
    """全局"""
    ALL_GROUPS = "ALL_GROUPS"
    """所有群组"""
    GROUP = "GROUP"
    """群组"""
    USER = "USER"
    """用户"""
    TAG = "TAG"
    """标签"""


class BaseTrigger(BaseModel):
    """触发器配置的基类"""

    trigger_type: str = Field(..., exclude=True)
    """触发器类型"""


class CronTrigger(BaseTrigger):
    """Cron 触发器配置"""

    trigger_type: Literal["cron"] = "cron"  # type: ignore
    """触发器类型"""
    year: int | str | None = None
    """年"""
    month: int | str | None = None
    """月"""
    day: int | str | None = None
    """日"""
    week: int | str | None = None
    """周"""
    day_of_week: int | str | None = None
    """星期几"""
    hour: int | str | None = None
    """小时"""
    minute: int | str | None = None
    """分钟"""
    second: int | str | None = None
    """秒"""
    start_date: datetime | str | None = None
    """开始日期"""
    end_date: datetime | str | None = None
    """结束日期"""
    timezone: str | None = None
    """时区"""
    jitter: int | None = None
    """运行抖动时间"""


class IntervalTrigger(BaseTrigger):
    """Interval 触发器配置"""

    trigger_type: Literal["interval"] = "interval"  # type: ignore
    """触发器类型"""
    weeks: int = 0
    """周数"""
    days: int = 0
    """天数"""
    hours: int = 0
    """小时数"""
    minutes: int = 0
    """分钟数"""
    seconds: int = 0
    """秒数"""
    start_date: datetime | str | None = None
    """开始日期"""
    end_date: datetime | str | None = None
    """结束日期"""
    timezone: str | None = None
    """时区"""
    jitter: int | None = None
    """运行抖动时间"""


class DateTrigger(BaseTrigger):
    """Date 触发器配置"""

    trigger_type: Literal["date"] = "date"  # type: ignore
    """触发器类型"""
    run_date: datetime | str
    """运行日期"""
    timezone: str | None = None
    """时区"""


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

    jitter: int | None = Field(None)
    """触发时间抖动(秒)"""
    spread: int | None = Field(None)
    """(并发模式)多目标执行的最大分散延迟(秒)"""
    interval: int | None = Field(None)
    """多目标执行的固定间隔(秒)，设置后将强制串行执行"""
    concurrency_policy: Literal["ALLOW", "SKIP", "QUEUE"] = Field("ALLOW")
    """并发策略"""
    retries: int = 0
    """重试次数"""
    retry_delay_seconds: int = 30
    """重试延迟时间(秒)"""


class ScheduleContext(BaseModel):
    """
    定时任务执行上下文，可通过依赖注入获取。
    """

    schedule_id: int = Field(...)
    """数据库中的任务ID"""
    plugin_name: str = Field(...)
    """任务所属的插件名称"""
    bot_id: str | None = Field(None)
    """执行任务的Bot ID"""
    platform_scope: str | None = Field(None)
    """执行任务的细粒度平台作用域"""
    group_id: str | None = Field(None)
    """当前执行实例的目标群组ID"""
    user_id: str | None = None
    """当前执行实例的目标用户ID（私聊场景）"""
    job_kwargs: dict = Field(default_factory=dict)
    """任务配置的参数"""


class ExecutionPolicy(BaseModel):
    """
    封装定时任务的执行策略，包括重试和回调。
    """

    retries: int = 0
    """重试次数"""
    retry_delay_seconds: int = 30
    """重试延迟时间(秒)"""
    retry_backoff: bool = False
    """是否使用退避算法延迟重试"""
    retry_on_exceptions: list[type[Exception]] | None = None
    """触发重试的异常类型列表"""
    on_success_callback: Callable[[ScheduleContext, Any], Awaitable[None]] | None = None
    """任务执行成功后的回调函数"""
    on_failure_callback: (
        Callable[[ScheduleContext, Exception], Awaitable[None]] | None
    ) = None
    """任务执行失败后的回调函数"""

    class Config:
        arbitrary_types_allowed = True


class JobConfig(BaseModel):
    """
    定时任务参数配置聚合实体 (Parameter Object)。
    封装了除核心身份标识(插件名、目标类型)之外的所有调度配置。
    """

    trigger: BaseTrigger
    """触发器配置"""
    job_kwargs: dict[str, Any] = Field(default_factory=dict)
    """任务执行参数"""
    bot_id: str | None = None
    """绑定的 Bot ID"""
    name: str | None = None
    """定时任务名称"""
    created_by: str | None = None
    """任务创建者标识"""
    required_permission: int = 5
    """执行任务所需的权限等级"""
    source: str = "USER"
    """任务来源"""
    is_one_off: bool = False
    """是否为一次性任务"""
    execution_options: ExecutionOptions = Field(
        default_factory=lambda: model_validate(ExecutionOptions, {})
    )
    """执行控制选项配置"""

    class Config:
        arbitrary_types_allowed = True


class ScheduledJobDeclaration(BaseModel):
    """用于在启动时声明默认定时任务的内部数据模型"""

    plugin_name: str
    """插件名称"""
    group_id: str | None
    """绑定的群组 ID"""
    bot_id: str | None
    """绑定的 Bot ID"""
    trigger: BaseTrigger
    """触发器配置"""
    job_kwargs: dict[str, Any]
    """任务执行参数"""

    class Config:
        arbitrary_types_allowed = True


class EphemeralJobDeclaration(BaseModel):
    """用于在启动时声明临时任务的内部数据模型"""

    plugin_name: str
    """插件名称"""
    func: Callable[..., Awaitable[Any]]
    """临时任务执行的异步函数"""
    trigger: BaseTrigger
    """触发器配置"""

    class Config:
        arbitrary_types_allowed = True

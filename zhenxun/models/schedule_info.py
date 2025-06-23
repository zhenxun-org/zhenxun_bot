from tortoise import fields

from zhenxun.services.db_context import Model


class ScheduleInfo(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    bot_id = fields.CharField(
        255, null=True, default=None, description="任务关联的Bot ID"
    )
    """任务关联的Bot ID"""
    plugin_name = fields.CharField(255, description="插件模块名")
    """插件模块名"""
    group_id = fields.CharField(
        255,
        null=True,
        description="群组ID, '__ALL_GROUPS__' 表示所有群, 为空表示全局任务",
    )
    """群组ID, 为空表示全局任务"""
    trigger_type = fields.CharField(
        max_length=20, default="cron", description="触发器类型 (cron, interval, date)"
    )
    """触发器类型 (cron, interval, date)"""
    trigger_config = fields.JSONField(description="触发器具体配置")
    """触发器具体配置"""
    job_kwargs = fields.JSONField(
        default=dict, description="传递给任务函数的额外关键字参数"
    )
    """传递给任务函数的额外关键字参数"""
    is_enabled = fields.BooleanField(default=True, description="是否启用")
    """是否启用"""
    create_time = fields.DatetimeField(auto_now_add=True)
    """创建时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "schedule_info"
        table_description = "通用定时任务表"

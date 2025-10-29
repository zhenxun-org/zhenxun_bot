from tortoise import fields

from zhenxun.services.db_context import Model


class ScheduledJob(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    name = fields.CharField(
        max_length=255, null=True, description="任务别名，方便用户辨识"
    )
    created_by = fields.CharField(
        max_length=255, null=True, description="创建任务的用户ID"
    )
    required_permission = fields.IntField(
        default=5, description="管理此任务所需的最低权限等级"
    )
    source = fields.CharField(
        max_length=50, default="USER", description="任务来源 (USER, PLUGIN_DEFAULT)"
    )

    bot_id = fields.CharField(
        255, null=True, description="执行任务的Bot约束 (具体Bot ID或平台)"
    )
    plugin_name = fields.CharField(255, description="插件模块名")
    target_type = fields.CharField(
        max_length=50, description="目标类型 (GROUP, USER, TAG, ALL_GROUPS, GLOBAL)"
    )
    target_identifier = fields.CharField(
        max_length=255, description="目标标识符 (群号, 标签名等)"
    )

    trigger_type = fields.CharField(
        max_length=20, default="cron", description="触发器类型 (cron, interval, date)"
    )
    trigger_config = fields.JSONField(description="触发器具体配置")
    job_kwargs = fields.JSONField(
        default=dict, description="传递给任务函数的额外关键字参数"
    )

    is_enabled = fields.BooleanField(default=True, description="是否启用")
    is_one_off = fields.BooleanField(default=False, description="是否为一次性任务")
    last_run_at = fields.DatetimeField(null=True, description="上次执行完成时间")
    last_run_status = fields.CharField(
        max_length=20, null=True, description="上次执行状态 (SUCCESS, FAILURE)"
    )
    consecutive_failures = fields.IntField(default=0, description="连续失败次数")
    execution_options = fields.JSONField(
        null=True,
        description="任务执行的额外选项 (例如: jitter, spread, "
        "interval, concurrency_policy)",
    )
    create_time = fields.DatetimeField(auto_now_add=True)

    class Meta:  # type: ignore
        table = "scheduled_tasks"
        table_description = "通用定时任务定义表"

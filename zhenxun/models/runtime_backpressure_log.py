from typing import ClassVar

from tortoise import fields

from zhenxun.services.db_context import Model


class RuntimeBackpressureLog(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    scope_key = fields.CharField(255, null=True, description="作用域")
    """作用域"""
    reason = fields.CharField(255, null=True, description="原因")
    """原因"""
    lane = fields.CharField(64, null=True, description="调度通道")
    """调度通道"""
    action = fields.CharField(64, description="处理动作")
    """处理动作 execute/skip/defer/signal"""
    queue_size = fields.IntField(default=0, description="队列长度")
    """队列长度"""
    active_count = fields.IntField(default=0, description="活跃数量")
    """活跃数量"""
    duration_ms = fields.FloatField(default=0, description="持续耗时毫秒")
    """持续耗时毫秒"""
    create_time = fields.DatetimeField(auto_now_add=True, description="创建时间")
    """创建时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "runtime_backpressure_log"
        table_description = "运行时背压追加审计日志"
        indexes: ClassVar = [
            ("create_time",),
            ("scope_key", "create_time"),
            ("lane", "create_time"),
        ]

    @classmethod
    async def _run_script(cls):
        return []

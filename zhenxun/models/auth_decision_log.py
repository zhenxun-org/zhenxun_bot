from typing import ClassVar

from tortoise import fields

from zhenxun.services.db_context import Model


class AuthDecisionLog(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    bot_id = fields.CharField(255, null=True, description="Bot ID")
    """Bot ID"""
    platform = fields.CharField(64, null=True, description="平台")
    """平台"""
    group_id = fields.CharField(255, null=True, description="群组id")
    """群组id"""
    user_id = fields.CharField(255, null=True, description="用户id")
    """用户id"""
    module = fields.CharField(255, null=True, description="插件模块")
    """插件模块"""
    effect = fields.CharField(32, description="决策结果")
    """决策结果 allow/deny/skip/defer/error"""
    reason = fields.CharField(255, null=True, description="原因")
    """原因"""
    shadow_effect = fields.CharField(32, null=True, description="影子决策结果")
    """影子决策结果"""
    shadow_reason = fields.CharField(255, null=True, description="影子决策原因")
    """影子决策原因"""
    side_effect_state = fields.TextField(null=True, description="副作用状态")
    """副作用状态 JSON 摘要"""
    latency_ms = fields.FloatField(default=0, description="耗时毫秒")
    """耗时毫秒"""
    overloaded = fields.BooleanField(default=False, description="是否过载")
    """是否过载"""
    create_time = fields.DatetimeField(auto_now_add=True, description="创建时间")
    """创建时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "auth_decision_log"
        table_description = "权限决策追加审计日志"
        indexes: ClassVar = [
            ("create_time",),
            ("module", "create_time"),
            ("effect", "create_time"),
        ]

    @classmethod
    async def _run_script(cls):
        return []

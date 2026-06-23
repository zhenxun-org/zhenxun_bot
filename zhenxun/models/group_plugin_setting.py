from tortoise import fields

from zhenxun.services.db_context import Model


class GroupPluginSetting(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增ID"""
    group_id = fields.CharField(max_length=255, indexed=True, description="群组ID")
    """群组ID"""
    plugin_name = fields.CharField(
        max_length=255, indexed=True, description="插件模块名"
    )
    """插件模块名"""
    settings = fields.JSONField(description="插件的完整配置 (JSON)")
    """插件的完整配置 (JSON)"""
    updated_at = fields.DatetimeField(auto_now=True, description="最后更新时间")
    """最后更新时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "group_plugin_settings"
        table_description = "插件分群通用配置表"
        unique_together = ("group_id", "plugin_name")

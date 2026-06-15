from typing import ClassVar

from tortoise import fields

from zhenxun.services.db_context import Model
from zhenxun.services.db_context.schema_ops import AlterColumnType, RenameColumn


class Statistics(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255)
    """用户id"""
    group_id = fields.CharField(255, null=True)
    """群聊id"""
    plugin_name = fields.CharField(255)
    """插件名称"""
    create_time = fields.DatetimeField(auto_now_add=True)
    """添加日期"""
    bot_id = fields.CharField(255, null=True)
    """Bot Id"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "statistics"
        table_description = "插件调用统计数据库"
        indexes: ClassVar = [
            ("user_id", "plugin_name"),
            ("group_id", "plugin_name"),
            ("plugin_name", "create_time"),
            ("user_id", "create_time"),
        ]

    @classmethod
    async def _run_script(cls):
        return [
            RenameColumn("statistics", "user_qq", "user_id"),
            # 将user_qq改为user_id
            AlterColumnType(
                "statistics",
                "user_id",
                {"postgres": "character varying(255)", "mysql": "VARCHAR(255)"},
            ),
            AlterColumnType(
                "statistics",
                "group_id",
                {"postgres": "character varying(255)", "mysql": "VARCHAR(255)"},
            ),
        ]

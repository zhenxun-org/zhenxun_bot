from typing import ClassVar

from tortoise import fields

from zhenxun.services.db_context import Model
from zhenxun.utils.enum import GoldHandle


class UserGoldLog(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255, description="用户id")
    """用户id"""
    gold = fields.IntField(description="金币")
    """金币"""
    handle = fields.CharEnumField(GoldHandle, default=None, description="道具处理类型")
    """金币处理类型"""
    source = fields.CharField(255, null=True, description="来源插件")
    """来源插件"""
    create_time = fields.DatetimeField(auto_now_add=True, description="创建时间")
    """创建时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "user_gold_log"
        table_description = "用户金币记录表"
        # 高频写表补索引(H4):按 user_id / (user_id, create_time) 查询流水,
        # SchemaGuard 启动时自动建,不改变查询语义。
        indexes: ClassVar = [
            ("user_id",),
            ("user_id", "create_time"),
        ]

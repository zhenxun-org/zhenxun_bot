from tortoise import fields

from zhenxun.services.db_context import Model
from zhenxun.utils.enum import EventLogType


class EventLog(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    user_id = fields.CharField(255, description="用户id")
    """用户id"""
    group_id = fields.CharField(255, description="群组id")
    """群组id"""
    event_type = fields.CharEnumField(EventLogType, default=None, description="类型")
    """类型"""
    create_time = fields.DatetimeField(auto_now_add=True, description="创建时间")
    """创建时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "event_log"
        table_description = "各种请求通知记录表"

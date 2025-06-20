from tortoise import fields

from zhenxun.services.db_context import Model
from zhenxun.utils.enum import BotSentType


class BotMessageStore(Model):
    id = fields.IntField(pk=True, generated=True, auto_increment=True)
    """自增id"""
    bot_id = fields.CharField(255, null=True)
    """bot id"""
    user_id = fields.CharField(255, null=True)
    """目标id"""
    group_id = fields.CharField(255, null=True)
    """群组id"""
    sent_type = fields.CharEnumField(BotSentType)
    """类型"""
    text = fields.TextField(null=True)
    """文本内容"""
    plain_text = fields.TextField(null=True)
    """纯文本"""
    platform = fields.CharField(255, null=True)
    """平台"""
    create_time = fields.DatetimeField(auto_now_add=True)
    """创建时间"""

    class Meta:  # pyright: ignore [reportIncompatibleVariableOverride]
        table = "bot_message_store"
        table_description = "Bot发送消息列表"

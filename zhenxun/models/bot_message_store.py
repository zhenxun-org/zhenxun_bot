from tortoise import fields

from zhenxun.services.db_context import Model
from zhenxun.utils.enum import BotSentType

from ._bot_message_buffer import append_bot_message_store_record


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

    @classmethod
    async def append_buffered(
        cls,
        *,
        bot_id: str | None = None,
        user_id: str | None = None,
        group_id: str | None = None,
        sent_type: BotSentType,
        text: str | None = None,
        plain_text: str | None = None,
        platform: str | None = None,
    ) -> None:
        await append_bot_message_store_record(
            cls(
                bot_id=bot_id,
                user_id=user_id,
                group_id=group_id,
                sent_type=sent_type,
                text=text,
                plain_text=plain_text,
                platform=platform,
            )
        )

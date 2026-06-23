from nonebot import on_message
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import UniMsg
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.models.chat_history import ChatHistory
from zhenxun.services.db_context import with_db_timeout
from zhenxun.services.log import logger
from zhenxun.services.low_priority_writer import (
    LowPriorityWriterConfig,
    append_low_priority_record,
    register_low_priority_writer,
)
from zhenxun.services.message_load import is_overloaded
from zhenxun.utils.enum import PluginType
from zhenxun.utils.utils import get_entity_ids

__plugin_meta__ = PluginMetadata(
    name="消息存储",
    description="消息存储，被动存储群消息",
    usage="",
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.HIDDEN,
        configs=[
            RegisterConfig(
                module="chat_history",
                key="FLAG",
                value=True,
                help="是否开启消息自从存储",
                default_value=True,
                type=bool,
            )
        ],
    ).to_dict(),
)


def rule(message: UniMsg) -> bool:
    return bool(Config.get_config("chat_history", "FLAG") and message)


chat_history = on_message(rule=rule, priority=1, block=False)

_WRITER_NAME = "chat_history"
_FLUSH_BATCH_SIZE = 200
_FLUSH_MAX_PER_TICK = 1000
_FLUSH_DB_TIMEOUT = 5.0


async def _write_chat_history_batch(batch: list[ChatHistory], reason: str) -> None:
    await with_db_timeout(
        ChatHistory.bulk_create(batch, _FLUSH_BATCH_SIZE),
        timeout=_FLUSH_DB_TIMEOUT,
        operation=f"ChatHistory.bulk_create[{len(batch)}]",
        source=f"chat_history:{reason}",
    )


register_low_priority_writer(
    LowPriorityWriterConfig(
        name=_WRITER_NAME,
        write_batch=_write_chat_history_batch,
        batch_size=_FLUSH_BATCH_SIZE,
        trigger_size=_FLUSH_BATCH_SIZE,
        max_retain=5000,
        flush_interval_seconds=60.0,
        max_items_per_cycle=_FLUSH_MAX_PER_TICK,
        backoff_base_seconds=30.0,
        backoff_max_seconds=600.0,
        log_command="chat_history",
    )
)


@chat_history.handle()
async def _(message: UniMsg, session: Uninfo):
    entity = get_entity_ids(session)
    if is_overloaded():
        return
    try:
        await append_low_priority_record(
            _WRITER_NAME,
            ChatHistory(
                user_id=entity.user_id,
                group_id=entity.group_id,
                text=str(message),
                plain_text=message.extract_plain_text(),
                bot_id=session.self_id,
                platform=session.platform,
            ),
        )
    except Exception as e:
        logger.warning("存储聊天记录失败", "chat_history", e=e)

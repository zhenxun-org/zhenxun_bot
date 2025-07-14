from nonebot import on_message
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import UniMsg
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.models.chat_history import ChatHistory
from zhenxun.services.log import logger
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

TEMP_LIST = []


@chat_history.handle()
async def _(message: UniMsg, session: Uninfo):
    entity = get_entity_ids(session)
    TEMP_LIST.append(
        ChatHistory(
            user_id=entity.user_id,
            group_id=entity.group_id,
            text=str(message),
            plain_text=message.extract_plain_text(),
            bot_id=session.self_id,
            platform=session.platform,
        )
    )


@scheduler.scheduled_job(
    "interval",
    minutes=1,
)
async def _():
    try:
        message_list = TEMP_LIST.copy()
        TEMP_LIST.clear()
        if message_list:
            await ChatHistory.bulk_create(message_list)
            logger.debug(f"批量添加聊天记录 {len(message_list)} 条", "定时任务")
    except Exception as e:
        logger.warning("存储聊天记录失败", "chat_history", e=e)

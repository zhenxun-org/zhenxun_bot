from typing import Any

from nonebot.adapters import Bot, Message

from zhenxun.configs.config import Config
from zhenxun.models.bot_message_store import BotMessageStore
from zhenxun.services.log import logger
from zhenxun.utils.enum import BotSentType
from zhenxun.utils.log_sanitizer import sanitize_for_logging
from zhenxun.utils.manager.message_manager import MessageManager
from zhenxun.utils.platform import PlatformUtils

LOG_COMMAND = "MessageHook"


def replace_message(message: Message) -> str:
    """将消息中的at、image、record、face替换为字符串

    参数:
        message: Message

    返回:
        str: 文本消息
    """
    result = ""
    for msg in message:
        if isinstance(msg, str):
            result += msg
        elif msg.type == "at":
            result += f"@{msg.data['qq']}"
        elif msg.type == "image":
            result += "[image]"
        elif msg.type == "record":
            result += "[record]"
        elif msg.type == "face":
            result += f"[face:{msg.data['id']}]"
        elif msg.type == "reply":
            result += ""
        else:
            result += str(msg)
    return result


@Bot.on_called_api
async def handle_api_result(
    bot: Bot, exception: Exception | None, api: str, data: dict[str, Any], result: Any
):
    if exception or api != "send_msg":
        return
    user_id = data.get("user_id")
    group_id = data.get("group_id")
    message_id = result.get("message_id")
    message: Message = data.get("message", "")
    message_type = data.get("message_type")
    try:
        if user_id and message_id:
            MessageManager.add(str(user_id), str(message_id))
            logger.debug(
                f"收集消息id，user_id: {user_id}, msg_id: {message_id}", LOG_COMMAND
            )
    except Exception as e:
        logger.warning(
            f"收集消息id发生错误...data: {data}, result: {result}", LOG_COMMAND, e=e
        )
    if not Config.get_config("hook", "RECORD_BOT_SENT_MESSAGES"):
        return
    try:
        await BotMessageStore.append_buffered(
            bot_id=bot.self_id,
            user_id=user_id,
            group_id=group_id,
            sent_type=BotSentType.GROUP
            if message_type == "group"
            else BotSentType.PRIVATE,
            text=replace_message(message),
            plain_text=message.extract_plain_text()
            if isinstance(message, Message)
            else replace_message(message),
            platform=PlatformUtils.get_platform(bot),
        )
        sanitized_message = sanitize_for_logging(message, context="nonebot_message")
        logger.debug(f"消息发送记录，message: {sanitized_message}")
    except Exception as e:
        logger.warning(
            f"消息发送记录发生错误...data: {data}, result: {result}",
            LOG_COMMAND,
            e=e,
        )

from collections.abc import Mapping
from typing import Any

from nonebot.adapters import Bot, Message

from zhenxun.services.log import logger
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
    if (
        exception
        or api != "send_msg"
        or PlatformUtils.get_platform_scope(bot) != "qq_client"
    ):
        return
    user_id = data.get("user_id")
    message_id = result.get("message_id") if isinstance(result, Mapping) else None
    message: Message = data.get("message", "")
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
    sanitized_message = sanitize_for_logging(message, context="nonebot_message")
    logger.debug(f"消息发送记录，message: {sanitized_message}")

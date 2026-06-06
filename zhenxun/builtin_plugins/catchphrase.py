from typing import Any

from nonebot.adapters import Bot

from zhenxun.configs.config import Config
from zhenxun.services.log import logger
from zhenxun.utils.platform import PlatformUtils

Config.add_plugin_config(
    "catchphrase",
    "CATCHPHRASE",
    "",
    help="小真寻的口癖，在文本末尾添加指定文字~",
    default_value="",
)


@Bot.on_calling_api
async def handle_api_call(bot: Bot, api: str, data: dict[str, Any]):
    if PlatformUtils.get_platform_scope(bot) != "qq_client":
        return
    if api == "send_msg":
        catchphrase = Config.get_config("catchphrase", "CATCHPHRASE")
        if catchphrase and (message := data.get("message")):
            for i in range(len(message) - 1, -1, -1):
                if message[i].type == "text":
                    message[i].data["text"] += catchphrase
                    logger.debug(
                        f"文本: {message[i].data['text']} 添加口癖: {catchphrase}"
                    )
                    break

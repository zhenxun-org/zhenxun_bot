import os
import random

from nonebot import on_message
from nonebot.adapters import Event
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import UniMsg
from nonebot_plugin_session import EventSession
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.path_config import IMAGE_PATH
from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.ban_console import BanConsole
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

__plugin_meta__ = PluginMetadata(
    name="笨蛋检测",
    description="功能名称当命令检测",
    usage="""当一些笨蛋直接输入功能名称时，提示笨蛋使用帮助指令查看功能帮助""".strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.DEPENDANT,
        menu_type="其他",
    ).to_dict(),
)


async def rule(event: Event, message: UniMsg, session: Uninfo) -> bool:
    group_id = session.group.id if session.group else None
    text = message.extract_plain_text().strip()
    if await BanConsole.is_ban(session.user.id, group_id):
        return False
    if group_id:
        if await BanConsole.is_ban(None, group_id):
            return False
        if g := await GroupConsole.get_group(group_id):
            if g.level < 0:
                return False
    return event.is_tome() and bool(text and len(text) < 20)


_matcher = on_message(rule=rule, priority=996, block=False)


_path = IMAGE_PATH / "_base" / "laugh"


@_matcher.handle()
async def _(matcher: Matcher, message: UniMsg, session: EventSession):
    text = message.extract_plain_text().strip()
    plugin = await PluginInfo.get_or_none(
        name=text,
        load_status=True,
        plugin_type=PluginType.NORMAL,
        block_type__isnull=True,
        status=True,
    )

    if not plugin:
        return

    image = None
    if _path.exists():
        if files := os.listdir(_path):
            image = _path / random.choice(files)
    message_list = []
    if image:
        message_list.append(image)
    message_list.append(
        "桀桀桀，预判到会有 '笨蛋' 把功能名称当命令用，特地前来嘲笑！"
        f"但还是好心来帮帮你啦！\n请at我发送 '帮助{plugin.name}' 或者"
        f" '帮助{plugin.id}' 来获取该功能帮助！"
    )
    logger.info("检测到功能名称当命令使用，已发送帮助信息", "功能帮助", session=session)
    await MessageUtils.build_message(message_list).send(reply_to=True)
    matcher.stop_propagation()

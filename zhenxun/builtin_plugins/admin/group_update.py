from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import Alconna, Arparma, on_alconna
from nonebot_plugin_session import EventSession

from zhenxun.configs.utils import PluginExtraData
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.rules import admin_check, ensure_group

__plugin_meta__ = PluginMetadata(
    name="更新群组列表",
    description="更新群组列表",
    usage="""
    更新群组的基本信息
    指令：
        更新群组信息
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.SUPER_AND_ADMIN,
        admin_level=1,
    ).to_dict(),
)


_matcher = on_alconna(
    Alconna("更新群组信息"),
    rule=admin_check(1) & ensure_group,
    priority=5,
    block=True,
)


@_matcher.handle()
async def _(bot: Bot, session: EventSession, arparma: Arparma):
    logger.info("更新群组信息", arparma.header_result, session=session)
    try:
        if PlatformUtils.get_platform_scope(bot) != "qq_client":
            await MessageUtils.build_message(
                "当前平台不支持旧群组信息同步，仅 OneBot 协议端可用。"
            ).send(reply_to=True)
            return
        await PlatformUtils.update_group(bot)
        await MessageUtils.build_message("已经成功更新了群组信息!").send(reply_to=True)
    except Exception:
        await MessageUtils.build_message("更新群组信息失败!").finish(reply_to=True)

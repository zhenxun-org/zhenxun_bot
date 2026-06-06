from nonebot.adapters import Bot
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from nonebot_plugin_alconna import Alconna, Arparma, on_alconna
from nonebot_plugin_session import EventSession

from zhenxun.configs.utils import PluginExtraData
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

__plugin_meta__ = PluginMetadata(
    name="更新群组/好友信息",
    description="更新群组/好友信息",
    usage="""
    更新群组信息
    更新好友信息
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.SUPERUSER,
    ).to_dict(),
)


_group_matcher = on_alconna(
    Alconna(
        "更新群组信息",
    ),
    permission=SUPERUSER,
    rule=to_me(),
    priority=1,
    block=True,
)

_friend_matcher = on_alconna(
    Alconna(
        "更新好友信息",
    ),
    permission=SUPERUSER,
    rule=to_me(),
    priority=1,
    block=True,
)


@_group_matcher.handle()
async def _(
    bot: Bot,
    session: EventSession,
    arparma: Arparma,
):
    try:
        if PlatformUtils.get_platform_scope(bot) != "qq_client":
            await MessageUtils.build_message(
                "当前平台不支持旧群组信息同步，仅 OneBot 协议端可用。"
            ).send()
            return
        num = await PlatformUtils.update_group(bot)
        logger.info(
            f"更新群聊信息完成，共更新了 {num} 个群组的信息!",
            arparma.header_result,
            session=session,
        )
        await MessageUtils.build_message(f"成功更新了 {num} 个群组的信息").send()
    except Exception as e:
        logger.error(
            "更新群组信息发生错误", arparma.header_result, session=session, e=e
        )
        await MessageUtils.build_message("其他未知错误...").send()


@_friend_matcher.handle()
async def _(
    bot: Bot,
    session: EventSession,
    arparma: Arparma,
):
    try:
        if PlatformUtils.get_platform_scope(bot) != "qq_client":
            await MessageUtils.build_message(
                "当前平台不支持旧好友信息同步，仅 OneBot 协议端可用。"
            ).send()
            return
        num = await PlatformUtils.update_friend(bot)
        logger.info(
            f"更新好友信息完成，共更新了 {num} 个好友的信息!",
            arparma.header_result,
            session=session,
        )
        await MessageUtils.build_message(f"成功更新了 {num} 个好友的信息").send()
    except Exception as e:
        logger.error(
            "更新好友信息发生错误", arparma.header_result, session=session, e=e
        )
        await MessageUtils.build_message("其他未知错误...").send()

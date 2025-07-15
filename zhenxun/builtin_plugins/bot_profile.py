from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from nonebot_plugin_alconna import Alconna, Arparma, on_alconna
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import BotConfig
from zhenxun.configs.utils import PluginExtraData
from zhenxun.services.log import logger
from zhenxun.utils.manager.bot_profile_manager import BotProfileManager
from zhenxun.utils.message import MessageUtils

__plugin_meta__ = PluginMetadata(
    name="自我介绍",
    description=f"这是{BotConfig.self_nickname}的深情告白",
    usage="""
    指令：
        自我介绍
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        menu_type="其他",
        superuser_help="""
        在data/bot_profile/bot_id/profile.txt 中编辑BOT自我介绍
        在data/bot_profile/bot_id/bot_id.png  中编辑BOT头像
        指令：
            重载自我介绍
        """.strip(),
    ).to_dict(),
)


_matcher = on_alconna(Alconna("自我介绍"), priority=5, block=True, rule=to_me())

_reload_matcher = on_alconna(
    Alconna("重载自我介绍"), priority=1, block=True, permission=SUPERUSER
)


@_matcher.handle()
async def _(session: Uninfo, arparma: Arparma):
    file_path = await BotProfileManager.build_bot_profile_image(session.self_id)
    if not file_path:
        await MessageUtils.build_message(
            f"{BotConfig.self_nickname}当前没有自我简介哦"
        ).finish(reply_to=True)
    await MessageUtils.build_message(file_path).send()
    logger.info("BOT自我介绍", arparma.header_result, session=session)


@_reload_matcher.handle()
async def _(session: Uninfo, arparma: Arparma):
    BotProfileManager.clear_profile_image(session.self_id)
    await MessageUtils.build_message(f"重载{BotConfig.self_nickname}自我介绍成功").send(
        reply_to=True
    )
    logger.info("重载BOT自我介绍", arparma.header_result, session=session)

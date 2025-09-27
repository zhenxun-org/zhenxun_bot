from nonebot import on_notice
from nonebot.adapters.onebot.v11 import PokeNotifyEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule, to_me
from nonebot_plugin_alconna import Alconna, on_alconna

from zhenxun import ui
from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.rules import notice_rule

from .data_source import get_status_info

__plugin_meta__ = PluginMetadata(
    name="服务器自我检查",
    description="查看服务器当前状态",
    usage="""
    查看服务器当前状态
    指令:
        自检
        戳一戳BOT
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.2",
        plugin_type=PluginType.SUPERUSER,
        configs=[
            RegisterConfig(
                key="type",
                value="mix",
                help="自检触发方式 ['message', 'poke', 'mix']",
                default_value="mix",
            )
        ],
    ).to_dict(),
)


def commandRule() -> Rule:
    return Rule(lambda: Config.get_config("check", "type") in {"message", "mix"})


def noticeRule() -> Rule:
    return Rule(lambda: Config.get_config("check", "type") in {"poke", "mix"})


_self_check_matcher = _self_check_matcher = on_alconna(
    Alconna("自检"),
    rule=to_me() & commandRule(),
    permission=SUPERUSER,
    block=True,
    priority=1,
)

_self_check_poke_matcher = on_notice(
    priority=5,
    permission=SUPERUSER,
    block=False,
    rule=notice_rule(PokeNotifyEvent) & to_me() & noticeRule(),
)


async def handle_self_check():
    try:
        data_dict = await get_status_info()

        image_bytes = await ui.render_template(
            "pages/builtin/check",
            data=data_dict,
        )

        await MessageUtils.build_message(image_bytes).send()
        logger.info("自检成功", "自检")
    except Exception as e:
        await MessageUtils.build_message(f"自检失败: {e}").send()
        logger.error("自检失败", "自检", e=e)


@_self_check_matcher.handle()
async def handle_message_check():
    await handle_self_check()


@_self_check_poke_matcher.handle()
async def handle_poke_check():
    await handle_self_check()

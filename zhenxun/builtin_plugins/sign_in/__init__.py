from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import (
    Alconna,
    AlconnaQuery,
    Args,
    Arparma,
    Option,
    Query,
    on_alconna,
    store_true,
)
from nonebot_plugin_apscheduler import scheduler

from zhenxun.configs.utils import (
    Command,
    PluginCdBlock,
    PluginExtraData,
    RegisterConfig,
)
from zhenxun.services.log import logger
from zhenxun.utils.depends import UserName
from zhenxun.utils.message import MessageUtils

from ._data_source import SignManage
from .goods_register import Uninfo
from .utils import clear_sign_data_pic

__plugin_meta__ = PluginMetadata(
    name="签到",
    description="每日签到，证明你在这里",
    usage="""
    每日签到
    会影响色图概率和开箱次数，以及签到的随机道具获取
    指令:
        签到
        我的签到
        好感度排行 ?[num=10]
        好感度总排行 ?[num=10]
    * 签到时有 3% 概率 * 2 *
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        commands=[
            Command(command="签到"),
            Command(command="我的签到"),
            Command(command="签到排行"),
            Command(command="签到总排行"),
        ],
        configs=[
            RegisterConfig(
                module="send_setu",
                key="INITIAL_SETU_PROBABILITY",
                value=0.7,
                help="初始色图概率，总概率 = 初始色图概率 + 好感度",
                default_value=0.7,
                type=float,
            ),
            RegisterConfig(
                key="MAX_SIGN_GOLD",
                value=200,
                help="签到好感度加成额外获得的最大金币数",
                default_value=200,
                type=int,
            ),
            RegisterConfig(
                key="SIGN_CARD1_PROB",
                value=0.2,
                help="签到好感度双倍加持卡Ⅰ掉落概率",
                default_value=0.2,
                type=float,
            ),
            RegisterConfig(
                key="SIGN_CARD2_PROB",
                value=0.09,
                help="签到好感度双倍加持卡Ⅲ掉落概率",
                default_value=0.09,
                type=float,
            ),
            RegisterConfig(
                key="SIGN_CARD3_PROB",
                value=0.05,
                help="签到好感度双倍加持卡Ⅲ掉落概率",
                default_value=0.05,
                type=float,
            ),
            RegisterConfig(
                key="IMAGE_STYLE",
                value="zhenxun",
                help="签到图片样式, [normal, zhenxun]",
                default_value="zhenxun",
            ),
        ],
        limits=[PluginCdBlock()],
    ).to_dict(),
)


_sign_matcher = on_alconna(
    Alconna(
        "签到",
        Option("--my", action=store_true, help_text="我的签到"),
        Option(
            "-l|--list",
            Args["num?", int],
            help_text="好感度排行",
        ),
        Option("-g|--global", action=store_true, help_text="全局排行"),
    ),
    priority=5,
    block=True,
)

_sign_matcher.shortcut(
    "我的签到",
    command="签到",
    arguments=["--my"],
    prefix=True,
)

_sign_matcher.shortcut(
    "好感度排行",
    command="签到",
    arguments=["--list"],
    prefix=True,
)

_sign_matcher.shortcut(
    "签到排行",
    command="签到",
    arguments=["--list"],
    prefix=True,
)

_sign_matcher.shortcut(
    "好感度总排行",
    command="签到",
    arguments=["--global", "--list"],
    prefix=True,
)

_sign_matcher.shortcut(
    "签到总排行",
    command="签到",
    arguments=["--global", "--list"],
    prefix=True,
)


@_sign_matcher.assign("$main")
async def _(session: Uninfo, arparma: Arparma, nickname: str = UserName()):
    path = await SignManage.sign(session, nickname)
    logger.info("签到成功", arparma.header_result, session=session)
    await MessageUtils.build_message(path).finish()


@_sign_matcher.assign("my")
async def _(session: Uninfo, arparma: Arparma, nickname: str = UserName()):
    path = await SignManage.sign(session, nickname, True)
    logger.info("查看我的签到", arparma.header_result, session=session)
    await MessageUtils.build_message(path).finish()


@_sign_matcher.assign("list")
async def _(
    session: Uninfo, arparma: Arparma, num: Query[int] = AlconnaQuery("num", 10)
):
    if num.result > 50:
        await MessageUtils.build_message("排行榜人数不能超过50哦...").finish()
    gid = session.group.id if session.group else None
    if not arparma.find("global") and not gid:
        await MessageUtils.build_message(
            "私聊中无法查看 '好感度排行'，请发送 '好感度总排行'"
        ).finish()
    if arparma.find("global"):
        gid = None
    image = await SignManage.rank(session, num.result, gid)
    logger.info("查看签到排行", arparma.header_result, session=session)
    await MessageUtils.build_message(image).send()


@scheduler.scheduled_job(
    "interval",
    hours=1,
)
async def _():
    try:
        clear_sign_data_pic()
        logger.info("清理日常签到图片数据数据完成...", "签到")
    except Exception as e:
        logger.error("清理日常签到图片数据数据失败...", e=e)

from arclet.alconna import Args
from nonebot.adapters import Bot
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import (
    Alconna,
    Arparma,
    At,
    Match,
    Option,
    on_alconna,
    store_true,
)
from nonebot_plugin_session import EventSession

from zhenxun.configs.config import BotConfig, Config
from zhenxun.configs.utils import (
    AICallableParam,
    AICallableProperties,
    AICallableTag,
    PluginExtraData,
    RegisterConfig,
)
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.rules import admin_check

from ._data_source import BanManage, call_ban

base_config = Config.get("ban")

__plugin_meta__ = PluginMetadata(
    name="Ban",
    description="你被逮捕了！丢进小黑屋！封禁用户以及群组，屏蔽消息",
    usage="""
    普通管理员
        格式:
        ban [At用户] ?[-t [时长(分钟)]]

        示例:
        ban @用户          : 永久拉黑用户
        ban @用户 -t 100   : 拉黑用户100分钟
        unban @用户        : 从小黑屋中拉出来
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.SUPER_AND_ADMIN,
        superuser_help="""
        超级管理员额外命令
        格式:
        ban [At用户/用户Id] ?[-t [时长]]
        unban --id [idx]  : 通过id来进行unban操作
        ban列表: 获取所有Ban数据

        群组ban列表: 获取群组Ban数据
        用户ban列表: 获取用户Ban数据

        ban列表 -u [用户Id]: 查找指定用户ban数据
        ban列表 -g [群组Id]: 查找指定群组ban数据
        示例:
            ban列表 -u 123456789    : 查找用户123456789的ban数据
            ban列表 -g 123456789    : 查找群组123456789的ban数据

        私聊下:
            示例:
            ban 123456789          : 永久拉黑用户123456789
            ban 123456789 -t 100   : 拉黑用户123456789 100分钟

            ban -g 999999              : 拉黑群组为999999的群组
            ban -g 999999 -t 100       : 拉黑群组为999999的群组 100分钟

            unban 123456789     : 从小黑屋中拉出来
            unban -g 999999     : 将群组9999999从小黑屋中拉出来
        """,
        admin_level=base_config.get("BAN_LEVEL", 5),
        configs=[
            RegisterConfig(
                key="BAN_LEVEL",
                value=5,
                help="ban/unban所需要的管理员权限等级",
                default_value=5,
                type=int,
            )
        ],
        smart_tools=[
            AICallableTag(
                name="call_ban",
                description="某人多次(至少三次)辱骂你，调用此方法进行封禁",
                parameters=AICallableParam(
                    type="object",
                    properties={
                        "user_id": AICallableProperties(
                            type="string", description="用户的id"
                        ),
                    },
                    required=["user_id"],
                ),
                func=call_ban,
            )
        ],
    ).to_dict(),
)


_ban_matcher = on_alconna(
    Alconna(
        "ban",
        Args["user?", [str, At]],
        Option("-g|--group", Args["group_id", str]),
        Option("-t|--time", Args["duration", int]),
    ),
    rule=admin_check("ban", "BAN_LEVEL"),
    priority=5,
    block=True,
)

_unban_matcher = on_alconna(
    Alconna(
        "unban",
        Args["user?", [str, At]],
        Option("-g|--group", Args["group_id", str]),
        Option("--id", Args["idx", int]),
    ),
    rule=admin_check("ban", "BAN_LEVEL"),
    priority=5,
    block=True,
)

_status_matcher = on_alconna(
    Alconna(
        "ban列表",
        Option("-u", Args["user_id", str], help_text="查找用户"),
        Option("-g", Args["group_id", str], help_text="查找群组"),
        Option("--user", action=store_true, help_text="过滤用户"),
        Option("--group", action=store_true, help_text="过滤群组"),
    ),
    permission=SUPERUSER,
    priority=1,
    block=True,
)

_status_matcher.shortcut(
    "用户ban列表",
    command="ban列表",
    arguments=["--user"],
    prefix=True,
)

_status_matcher.shortcut(
    "群组ban列表",
    command="ban列表",
    arguments=["--group"],
    prefix=True,
)


@_status_matcher.handle()
async def _(
    arparma: Arparma,
    user_id: Match[str],
    group_id: Match[str],
):
    filter_type = None
    if arparma.find("user"):
        filter_type = "user"
    if arparma.find("group"):
        filter_type = "group"
    _user_id = user_id.result if user_id.available else None
    _group_id = group_id.result if group_id.available else None
    if image := await BanManage.build_ban_image(filter_type, _user_id, _group_id):
        await MessageUtils.build_message(image).finish(reply_to=True)
    else:
        await MessageUtils.build_message("数据为空捏...").finish(reply_to=True)


@_ban_matcher.handle()
async def _(
    bot: Bot,
    session: EventSession,
    arparma: Arparma,
    user: Match[str | At],
    duration: Match[int],
    group_id: Match[str],
):
    user_id = ""
    if not session.id1:
        await MessageUtils.build_message("用户id为空...").finish(reply_to=True)
    if user.available:
        if isinstance(user.result, At):
            user_id = user.result.target
        else:
            if session.id1 not in bot.config.superusers:
                await MessageUtils.build_message("权限不足捏...").finish(reply_to=True)
            user_id = user.result
    _duration = duration.result * 60 if duration.available else -1
    _duration_text = f"{duration.result} 分钟" if duration.available else " 到世界湮灭"
    if (gid := session.id3 or session.id2) and not group_id.available:
        if not user_id or (
            user_id == bot.self_id and session.id1 not in bot.config.superusers
        ):
            _duration = 0.5
            await MessageUtils.build_message("倒反天罡，小小管理速速退下！").send()
            await BanManage.ban(session.id1, gid, 30, session, True)
            _duration_text = "半 分钟"
            logger.info(
                f"尝试ban {BotConfig.self_nickname} 反被拿下",
                arparma.header_result,
                session=session,
            )
            await MessageUtils.build_message(
                [
                    "对 ",
                    At(flag="user", target=session.id1),
                    " 狠狠惩戒了一番，一脚踢进了小黑屋!"
                    f" 在里面乖乖呆 {_duration_text}吧!",
                ]
            ).finish(reply_to=True)
        await BanManage.ban(
            user_id, gid, _duration, session, session.id1 in bot.config.superusers
        )
        logger.info(
            "管理员Ban",
            arparma.header_result,
            session=session,
            target=f"{gid}:{user_id}",
        )
        await MessageUtils.build_message(
            [
                "对 ",
                (
                    At(flag="user", target=user_id)
                    if isinstance(user.result, At)
                    else user_id
                ),  # type: ignore
                " 狠狠惩戒了一番，一脚踢进了小黑屋!",
                f" 在里面乖乖呆 {_duration_text} 吧!",
            ]
        ).finish(reply_to=True)
    elif session.id1 in bot.config.superusers:
        _group_id = group_id.result if group_id.available else None
        await BanManage.ban(user_id, _group_id, _duration, session, True)
        logger.info(
            "超级用户Ban",
            arparma.header_result,
            session=session,
            target=f"{_group_id}:{user_id}",
        )
        at_msg = user_id or f"群组:{_group_id}"
        await MessageUtils.build_message(
            f"对 {at_msg} 狠狠惩戒了一番，一脚踢进了小黑屋!"
        ).finish(reply_to=True)


@_unban_matcher.handle()
async def _(
    bot: Bot,
    session: EventSession,
    arparma: Arparma,
    user: Match[str | At],
    group_id: Match[str],
    idx: Match[int],
):
    user_id = ""
    _idx = idx.result if idx.available else None
    if user.available:
        if isinstance(user.result, At):
            user_id = user.result.target
        else:
            if session.id1 not in bot.config.superusers:
                await MessageUtils.build_message("权限不足捏...").finish(reply_to=True)
            user_id = user.result
    if gid := session.id3 or session.id2:
        if group_id.available:
            gid = group_id.result
        is_unban, result = await BanManage.unban(
            user_id, gid, session, _idx, session.id1 in bot.config.superusers
        )
        if not is_unban:
            await MessageUtils.build_message(result).finish(reply_to=True)
        logger.info(
            "管理员UnBan",
            arparma.header_result,
            session=session,
            target=f"{gid}:{result}",
        )
        await MessageUtils.build_message(
            [
                "将 ",
                (
                    At(flag="user", target=user_id)
                    if isinstance(user.result, At)
                    else result
                ),  # type: ignore
                " 从黑屋中拉了出来并急救了一下!",
            ]
        ).finish(reply_to=True)
    elif session.id1 in bot.config.superusers:
        _group_id = group_id.result if group_id.available else None
        is_unban, result = await BanManage.unban(
            user_id, _group_id, session, _idx, True
        )
        if not is_unban:
            await MessageUtils.build_message(result).finish(reply_to=True)
        logger.info(
            "超级用户UnBan",
            arparma.header_result,
            session=session,
            target=f"{_group_id}:{user_id}",
        )
        at_msg = user_id or f"群组:{result}"
        await MessageUtils.build_message(
            f"对 {at_msg} 从黑屋中拉了出来并急救了一下!"
        ).finish(reply_to=True)

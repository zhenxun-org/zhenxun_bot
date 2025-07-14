from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Bot as v11Bot
from nonebot.params import Depends
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.typing import T_State
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Arparma,
    Match,
    Option,
    Subcommand,
    on_alconna,
    store_true,
)
from nonebot_plugin_session import EventSession

from zhenxun.configs.config import BotConfig
from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.group_console import GroupConsole
from zhenxun.services.log import logger
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils

__plugin_meta__ = PluginMetadata(
    name="管理群操作",
    description="管理群操作",
    usage="""
    群权限 | 群白名单 | 退出群 操作
    退群，添加/删除群白名单，添加/删除群认证，当在群组中这五个命令且没有指定群号时，默认指定当前群组
    指令:
        格式:
        group-manage modify-level [权限等级] ?[群组Id]      : 修改群权限
        group-manage super-handle [群组Id] [--del 删除操作] : 添加/删除群白名单
        group-manage auth-handle [群组Id] [--del 删除操作]  : 添加/删除群认证
        group-manage del-group [群组Id]                    : 退出指定群

        快捷:
        group-manage modify-level : 修改群权限
        group-manage super-handle : 添加/删除群白名单
        group-manage auth-handle  : 添加/删除群认证
        group-manage del-group    : 退群

        示例:
        修改群权限 7                              : 在群组中修改当前群组权限为7
        修改群权限 7 1234556                     : 修改 123456 群组的权限等级为7
        添加/删除群白名单 1234567                  : 添加/删除 1234567 为群白名单
        添加/删除群认证 1234567                    : 添加/删除 1234567 为群认证
        退群 12344566                            : 退出指定群组
    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.SUPERUSER,
    ).to_dict(),
)


_matcher = on_alconna(
    Alconna(
        "group-manage",
        Option("--delete", action=store_true, help_text="删除"),
        Subcommand(
            "modify-level", Args["level", int]["group_id?", int], help_text="修改群权限"
        ),
        Subcommand(
            "super-handle",
            Args["group_id", int],
            help_text="添加/删除群白名单",
        ),
        Subcommand(
            "auth-handle",
            Args["group_id", int],
            help_text="添加/删除群认证",
        ),
        Subcommand("del-group", Args["group_id", int], help_text="退出群组"),
    ),
    permission=SUPERUSER,
    priority=1,
    block=True,
)

_matcher.shortcut(
    r"修改群权限\s?(?P<level>-?\d+)\s?(?P<group_id>\d+)?",
    command="group-manage",
    arguments=["modify-level", "{level}", "{group_id}"],
    prefix=True,
)

_matcher.shortcut(
    "添加群白名单",
    command="group-manage",
    arguments=["super-handle", "{%0}"],
    prefix=True,
)

_matcher.shortcut(
    "删除群白名单",
    command="group-manage",
    arguments=["super-handle", "{%0}", "--delete"],
    prefix=True,
)

_matcher.shortcut(
    "添加群认证",
    command="group-manage",
    arguments=["auth-handle", "{%0}"],
    prefix=True,
)

_matcher.shortcut(
    "删除群认证",
    command="group-manage",
    arguments=["auth-handle", "{%0}", "--delete"],
    prefix=True,
)

_matcher.shortcut(
    "退群",
    command="group-manage",
    arguments=["del-group", "{%0}"],
    prefix=True,
)


def CheckGroupId():
    """
    检测群组id
    """

    async def dependency(
        session: EventSession,
        group_id: Match[int],
        state: T_State,
    ):
        gid = session.id3 or session.id2
        if group_id.available:
            gid = group_id.result
        if not gid:
            await MessageUtils.build_message("群组id不能为空...").finish()
        state["group_id"] = gid

    return Depends(dependency)


@_matcher.assign("modify-level", parameterless=[CheckGroupId()])
async def _(session: EventSession, arparma: Arparma, state: T_State, level: int):
    gid = state["group_id"]
    group, _ = await GroupConsole.get_or_create(group_id=gid)
    old_level = group.level
    group.level = level
    await group.save(update_fields=["level"])
    await MessageUtils.build_message("群权限修改成功!").send(reply_to=True)
    logger.info(
        f"修改群权限: {old_level} -> {level}",
        arparma.header_result,
        session=session,
        target=gid,
    )


@_matcher.assign("super-handle", parameterless=[CheckGroupId()])
async def _(session: EventSession, arparma: Arparma, state: T_State):
    gid = state["group_id"]
    group = await GroupConsole.get_group(group_id=gid)
    if not group:
        await MessageUtils.build_message("群组信息不存在, 请更新群组信息...").finish()
    s = "删除" if arparma.find("delete") else "添加"
    group.is_super = not arparma.find("delete")
    await group.save(update_fields=["is_super"])
    await MessageUtils.build_message(f"{s}群白名单成功!").send(reply_to=True)
    logger.info(f"{s}群白名单", arparma.header_result, session=session, target=gid)


@_matcher.assign("auth-handle", parameterless=[CheckGroupId()])
async def _(session: EventSession, arparma: Arparma, state: T_State):
    gid = state["group_id"]
    await GroupConsole.update_or_create(
        group_id=gid,
        channel_id__isnull=True,
        defaults={"group_flag": 0 if arparma.find("delete") else 1},
    )
    s = "删除" if arparma.find("delete") else "添加"
    await MessageUtils.build_message(f"{s}群认证成功!").send(reply_to=True)
    logger.info(f"{s}群白名单", arparma.header_result, session=session, target=gid)


@_matcher.assign("del-group")
async def _(bot: Bot, session: EventSession, arparma: Arparma, group_id: int):
    if isinstance(bot, v11Bot):
        group_list = [g["group_id"] for g in await bot.get_group_list()]
        if group_id not in group_list:
            logger.debug("群组不存在", "退群", session=session, target=group_id)
            await MessageUtils.build_message(
                f"{BotConfig.self_nickname}未在该群组中..."
            ).finish()
        try:
            await bot.set_group_leave(group_id=group_id)
            logger.info(
                f"{BotConfig.self_nickname}退出群组成功",
                "退群",
                session=session,
                target=group_id,
            )
            await MessageUtils.build_message(f"退出群组 {group_id} 成功!").send()
            await GroupConsole.filter(group_id=group_id).delete()
        except Exception as e:
            logger.error("退出群组失败", "退群", session=session, target=group_id, e=e)
            await MessageUtils.build_message(f"退出群组 {group_id} 失败...").send()
    else:
        # TODO: 其他平台的退群操作
        await MessageUtils.build_message("暂未支持退群操作...").send()

from nonebot.adapters import Bot
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import AlconnaQuery, Arparma, Match, Query
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from zhenxun.utils.enum import BlockType, PluginType
from zhenxun.utils.message import MessageUtils

from ._data_source import PluginManager, build_plugin, build_task, delete_help_image
from .command import _group_status_matcher, _status_matcher

base_config = Config.get("plugin_switch")


__plugin_meta__ = PluginMetadata(
    name="功能开关",
    description="对群组内的功能限制，超级用户可以对群组以及全局的功能被动开关限制",
    usage="""
    普通管理员
        格式:
        开启/关闭[功能名称]         : 开关功能
        开启/关闭群被动[被动名称]    : 群被动开关
        开启/关闭所有插件           : 开启/关闭当前群组所有插件状态
        开启/关闭所有群被动         : 开启/关闭当前群组所有群被动
        群被动状态                 : 查看被动技能开关状态
        醒来                      : 结束休眠
        休息吧                    : 群组休眠, 不会再响应命令

        示例:
        开启签到              : 开启签到
        关闭签到              : 关闭签到
        开启群被动早晚安       : 关闭被动任务早晚安

    """.strip(),
    extra=PluginExtraData(
        author="HibiKier",
        version="0.1",
        plugin_type=PluginType.SUPER_AND_ADMIN,
        superuser_help="""
        格式:
        插件列表
        开启/关闭[功能名称] ?[-t ["private", "p", "group", "g"](关闭类型)] ?[-g 群组Id]

        开启/关闭插件df[功能名称]: 开启/关闭指定插件进群默认状态
        开启/关闭所有插件df: 开启/关闭所有插件进群默认状态
        开启/关闭所有插件:
            私聊中: 开启/关闭所有插件全局状态
            群组中: 开启/关闭当前群组所有插件状态

        开启/关闭群被动[name] ?[-g [group_id]]
            私聊中: 开启/关闭全局指定的被动状态
            群组中: 开启/关闭当前群组指定的被动状态
            示例:
                关闭群被动早晚安
                关闭群被动早晚安 -g 12355555

        开启/关闭默认群被动 [被动名称]
            私聊下: 开启/关闭群被动默认状态
            示例:
                关闭默认群被动 早晚安

        开启/关闭所有群被动 ?[-g [group_id]]
            私聊中: 开启/关闭全局或指定群组被动状态
            示例:
                开启所有群被动: 开启全局所有被动
                开启所有群被动 -g 12345678: 开启群组12345678所有被动

        私聊下:
            示例:
            开启签到                : 全局开启签到
            关闭签到                : 全局关闭签到
            关闭签到 p              : 全局私聊关闭签到
            关闭签到 -g 12345678    : 关闭群组12345678的签到功能(普通管理员无法开启)
        """,
        admin_level=base_config.get("CHANGE_GROUP_SWITCH_LEVEL", 2),
        configs=[
            RegisterConfig(
                key="CHANGE_GROUP_SWITCH_LEVEL",
                value=2,
                help="开关群功能权限",
                default_value=2,
                type=int,
            )
        ],
    ).to_dict(),
)


@_status_matcher.assign("$main")
async def _(
    bot: Bot,
    session: Uninfo,
    arparma: Arparma,
):
    if session.user.id in bot.config.superusers:
        image = await build_plugin()
        logger.info(
            "查看功能列表",
            arparma.header_result,
            session=session,
        )
        await MessageUtils.build_message(image).finish(reply_to=True)
    else:
        await MessageUtils.build_message("权限不足捏...").finish(reply_to=True)


@_status_matcher.assign("open")
async def _(
    bot: Bot,
    session: Uninfo,
    arparma: Arparma,
    plugin_name: Match[str],
    group: Match[str],
    task: Query[bool] = AlconnaQuery("task.value", False),
    default_status: Query[bool] = AlconnaQuery("default.value", False),
    all: Query[bool] = AlconnaQuery("all.value", False),
):
    if not all.result and not plugin_name.available:
        await MessageUtils.build_message("请输入功能/被动名称").finish(reply_to=True)
    name = plugin_name.result
    if session.group:
        group_id = session.group.id
        """修改当前群组的数据"""
        if task.result:
            if all.result:
                result = await PluginManager.unblock_group_all_task(group_id)
                logger.info("开启所有群组被动", arparma.header_result, session=session)
            else:
                result = await PluginManager.unblock_group_task(name, group_id)
                logger.info(
                    f"开启群组被动 {name}", arparma.header_result, session=session
                )
        elif session.user.id in bot.config.superusers and default_status.result:
            """单个插件的进群默认修改"""
            result = await PluginManager.set_default_status(name, True)
            logger.info(
                f"超级用户开启 {name} 功能进群默认开关",
                arparma.header_result,
                session=session,
            )
        elif all.result:
            """所有插件"""
            result = await PluginManager.set_all_plugin_status(
                True, default_status.result, group_id
            )
            logger.info(
                "开启群组中全部功能",
                arparma.header_result,
                session=session,
            )
        else:
            result = await PluginManager.unblock_group_plugin(name, group_id)
            logger.info(f"开启功能 {name}", arparma.header_result, session=session)
        delete_help_image(group_id)
        await MessageUtils.build_message(result).finish(reply_to=True)
    elif session.user.id in bot.config.superusers:
        """私聊"""
        group_id = group.result if group.available else None
        if all.result:
            if task.result:
                """关闭全局或指定群全部被动"""
                if group_id:
                    result = await PluginManager.unblock_group_all_task(group_id)
                else:
                    result = await PluginManager.unblock_global_all_task(
                        default_status.result
                    )
            else:
                result = await PluginManager.set_all_plugin_status(
                    True, default_status.result, group_id
                )
                logger.info(
                    "超级用户开启全部功能全局开关"
                    f" {f'指定群组: {group_id}' if group_id else ''}",
                    arparma.header_result,
                    session=session,
                )
            await MessageUtils.build_message(result).finish(reply_to=True)
        if default_status.result and not task.result:
            result = await PluginManager.set_default_status(name, True)
            logger.info(
                f"超级用户开启 {name} 功能进群默认开关",
                arparma.header_result,
                session=session,
                target=group_id,
            )
            await MessageUtils.build_message(result).finish(reply_to=True)
        if task.result:
            split_list = name.split()
            if len(split_list) > 1:
                name = split_list[0]
                group_id = split_list[1]
            if group_id:
                result = await PluginManager.superuser_task_handle(name, group_id, True)
                logger.info(
                    f"超级用户开启被动技能 {name}",
                    arparma.header_result,
                    session=session,
                    target=group_id,
                )
            else:
                result = await PluginManager.unblock_global_task(
                    name, default_status.result
                )
                logger.info(
                    f"超级用户开启全局被动技能 {name}",
                    arparma.header_result,
                    session=session,
                )
        else:
            result = await PluginManager.superuser_unblock(name, None, group_id)
            logger.info(
                f"超级用户开启功能 {name}",
                arparma.header_result,
                session=session,
                target=group_id,
            )
        delete_help_image()
        await MessageUtils.build_message(result).finish(reply_to=True)


@_status_matcher.assign("close")
async def _(
    bot: Bot,
    session: Uninfo,
    arparma: Arparma,
    plugin_name: Match[str],
    block_type: Match[str],
    group: Match[str],
    task: Query[bool] = AlconnaQuery("task.value", False),
    default_status: Query[bool] = AlconnaQuery("default.value", False),
    all: Query[bool] = AlconnaQuery("all.value", False),
):
    if not all.result and not plugin_name.available:
        await MessageUtils.build_message("请输入功能/被动名称").finish(reply_to=True)
    name = plugin_name.result
    if session.group:
        group_id = session.group.id
        """修改当前群组的数据"""
        if task.result:
            if all.result:
                result = await PluginManager.block_group_all_task(group_id)
                logger.info("开启所有群组被动", arparma.header_result, session=session)
            else:
                result = await PluginManager.block_group_task(name, group_id)
                logger.info(
                    f"关闭群组被动 {name}", arparma.header_result, session=session
                )
        elif session.user.id in bot.config.superusers and default_status.result:
            """单个插件的进群默认修改"""
            result = await PluginManager.set_default_status(name, False)
            logger.info(
                f"超级用户开启 {name} 功能进群默认开关",
                arparma.header_result,
                session=session,
            )
        elif all.result:
            """所有插件"""
            result = await PluginManager.set_all_plugin_status(
                False, default_status.result, group_id
            )
            logger.info("关闭群组中全部功能", arparma.header_result, session=session)
        else:
            result = await PluginManager.block_group_plugin(name, group_id)
            logger.info(f"关闭功能 {name}", arparma.header_result, session=session)
        delete_help_image(group_id)
        await MessageUtils.build_message(result).finish(reply_to=True)
    elif session.user.id in bot.config.superusers:
        group_id = group.result if group.available else None
        if all.result:
            if task.result:
                """关闭全局或指定群全部被动"""
                if group_id:
                    result = await PluginManager.block_group_all_task(group_id)
                else:
                    result = await PluginManager.block_global_all_task(
                        default_status.result
                    )
            else:
                result = await PluginManager.set_all_plugin_status(
                    False, default_status.result, group_id
                )
                logger.info(
                    "超级用户关闭全部功能全局开关"
                    f" {f'指定群组: {group_id}' if group_id else ''}",
                    arparma.header_result,
                    session=session,
                )
            await MessageUtils.build_message(result).finish(reply_to=True)
        if default_status.result and not task.result:
            result = await PluginManager.set_default_status(name, False)
            logger.info(
                f"超级用户关闭 {name} 功能进群默认开关",
                arparma.header_result,
                session=session,
                target=group_id,
            )
            await MessageUtils.build_message(result).finish(reply_to=True)
        if task.result:
            split_list = name.split()
            if len(split_list) > 1:
                name = split_list[0]
                group_id = split_list[1]
            if group_id:
                result = await PluginManager.superuser_task_handle(
                    name, group_id, False
                )
                logger.info(
                    f"超级用户关闭被动技能 {name}",
                    arparma.header_result,
                    session=session,
                    target=group_id,
                )
            else:
                result = await PluginManager.block_global_task(
                    name, default_status.result
                )
                logger.info(
                    f"超级用户关闭全局被动技能 {name}",
                    arparma.header_result,
                    session=session,
                )
        else:
            _type = BlockType.ALL
            if block_type.result in ["p", "private"]:
                if block_type.available:
                    _type = BlockType.PRIVATE
            elif block_type.result in ["g", "group"]:
                if block_type.available:
                    _type = BlockType.GROUP
            result = await PluginManager.superuser_block(name, _type, group_id)
            logger.info(
                f"超级用户关闭功能 {name}, 禁用类型: {_type}",
                arparma.header_result,
                session=session,
                target=group_id,
            )
        delete_help_image()
        await MessageUtils.build_message(result).finish(reply_to=True)


@_group_status_matcher.handle()
async def _(
    session: Uninfo,
    arparma: Arparma,
    status: str,
):
    if session.group:
        group_id = session.group.id
        if status == "sleep":
            await PluginManager.sleep(group_id)
            logger.info("进行休眠", arparma.header_result, session=session)
            await MessageUtils.build_message("那我先睡觉了...").finish()
        else:
            if await PluginManager.is_wake(group_id):
                await MessageUtils.build_message("我还醒着呢！").finish()
            await PluginManager.wake(group_id)
            logger.info("醒来", arparma.header_result, session=session)
            await MessageUtils.build_message("呜..醒来了...").finish()
    return MessageUtils.build_message("群组id为空...").send()


@_status_matcher.assign("task")
async def _(
    session: Uninfo,
    arparma: Arparma,
):
    image = await build_task(session.group.id if session.group else None)
    if image:
        logger.info("查看群被动列表", arparma.header_result, session=session)
        await MessageUtils.build_message(image).finish(reply_to=True)
    else:
        await MessageUtils.build_message("获取群被动任务失败...").finish(reply_to=True)

from nonebot.adapters import Bot, Event
from nonebot.exception import FinishedException
from nonebot.permission import SUPERUSER as SUPERUSER_PERM
from nonebot.plugin import PluginMetadata
from nonebot_plugin_alconna import AlconnaMatch, AlconnaQuery, Arparma, Match, Query
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config
from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from zhenxun.services.tags import tag_manager
from zhenxun.utils.enum import BlockType, PluginType
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

from .command import _group_status_matcher, _status_matcher
from .data_source import PluginManager
from .ui import (
    build_plugin,
    build_task,
    render_global_status,
    render_group_active_status,
)

base_config = Config.get("plugin_switch")


__plugin_meta__ = PluginMetadata(
    name="功能开关",
    description="对群组内的功能限制，超级用户可以对群组以及全局的功能被动开关限制",
    usage="""### 基础开关控制
- `开启/关闭 [功能名...]`：在当前群开启/关闭指定功能
- `开启/关闭被动 [被动名...]`：在当前群开启/关闭指定被动
- `开启/关闭所有功能`：在当前群开启/关闭所有功能
- `开启/关闭所有被动`：在当前群开启/关闭所有被动

**操作示例：**
- `关闭 签到 抽卡 色图`：在当前群批量关闭指定功能

### 机器人状态控制
- `醒来`：让机器人在当前群恢复工作
- `休息吧`：让机器人在当前群进入休眠状态
""",
    extra=PluginExtraData(
        author="HibiKier",
        version="1.0",
        plugin_type=PluginType.SUPER_AND_ADMIN,
        admin_level=base_config.get("CHANGE_GROUP_SWITCH_LEVEL", 2),
        superuser_help="""### 状态查询
- `插件列表`：查看所有插件的全局状态、群聊状态
- `被动状态`：查看所有被动技能的状态
- `查看功能状态 [功能名]`：查看指定功能在所有群组中的开关状态
- `查看被动状态 [被动名]`：查看指定被动在所有群组中的开关状态
- `查看群状态`：查看所有群组的休眠/工作状态

### 高级开关控制 (跨群/全局)
支持在指令后追加以下参数进行批量操作：
- `-g <群号>`：指定操作目标群（可多个）
- `-t <标签>`：指定操作带有特定标签的群
- `--all`：操作所有群组
- `--only`：白名单模式，仅在指定群组开启，其他群组自动关闭
- `-s`：**强制管控**。使用系统级字段禁用功能，群管理员无法通过普通指令自行开启

**操作示例：**
- `关闭 签到 抽卡 -t 游戏群`：关闭所有带有"游戏群"标签的群的签到和抽卡功能
- `开启 色图 --only -g 123456 654321`：仅在这两个群开启色图，其余群全部关闭
- `关闭 色图 -s`：在当前群强制锁定关闭色图，群管无法开启

### 系统级开关
追加 `--type [范围]` 或使用特定快捷词实现系统级控制。
范围：`p` (私聊), `g` (所有群聊), `a` (全局)

- `关闭 签到 --type a`：全局彻底禁用签到功能
- `开启/关闭默认 [功能名]`：修改功能进群时的默认开关状态
- `开启/关闭所有默认功能`：批量修改所有功能的进群默认状态

### 强制唤醒/休眠
同样支持高级目标参数。
- `休息吧 --all`：所有群组进入休眠
- `醒来 -t 内部测试群`：唤醒带有该标签的群组
""",
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


async def get_target_groups(
    bot: Bot,
    event: Event,
    session: Uninfo,
    tag: str | None,
    groups: tuple[str, ...] | None,
    all_scope: bool,
) -> set[str] | None:
    """解析目标群组列表，包含标签、群号和全量选项。"""
    targets: set[str] = set()
    is_superuser = await SUPERUSER_PERM(bot, event)

    if (tag or groups or all_scope) and not is_superuser:
        return None

    if groups:
        targets.update(str(group_id) for group_id in groups if group_id)

    if tag:
        tag_groups = await tag_manager.resolve_tag_to_group_ids(tag, bot=bot)
        targets.update(str(group_id) for group_id in tag_groups)

    if all_scope:
        all_groups, _ = await PlatformUtils.get_group_list(bot)
        targets.update(str(group.group_id) for group in all_groups if group.group_id)

    if not targets and session.group:
        targets.add(str(session.group.id))

    return targets


async def _handle_switch_command(
    status: bool,
    bot: Bot,
    event: Event,
    session: Uninfo,
    arparma: Arparma,
    plugin_names: Match[tuple[str, ...]],
    groups: Match[tuple[str, ...]] = AlconnaMatch("groups"),
    tag: Match[str] = AlconnaMatch("tag"),
    task: Query[bool] = AlconnaQuery("task.value", False),
    default_status: Query[bool] = AlconnaQuery("default.value", False),
    all_groups_flag: Query[bool] = AlconnaQuery("all.value", False),
    all_plugins_flag: Query[bool] = AlconnaQuery("all-plugins.value", False),
    only_flag: Query[bool] | None = None,
    use_su_field: Query[bool] = AlconnaQuery("su.value", False),
):
    is_superuser = await SUPERUSER_PERM(bot, event)
    only_flag_value = only_flag.result if only_flag else False

    is_remote = bool(
        tag.available or groups.available or all_groups_flag.result or only_flag_value
    )
    use_su_field_final = is_remote or use_su_field.result

    sub_name = "open" if status else "close"
    block_type_val = arparma.query(f"{sub_name}.type.block_type")

    if block_type_val is not None:
        if not is_superuser:
            return
        if task.result:
            await MessageUtils.build_message(
                "被动技能不支持指定禁用范围，请直接使用 开启/关闭"
            ).finish(reply_to=True)

    if not all_plugins_flag.result and not plugin_names.available:
        await MessageUtils.build_message("请输入功能/被动名称").finish(reply_to=True)

    targets = await get_target_groups(
        bot,
        event,
        session,
        tag.result if tag and tag.available else None,
        groups.result if groups and groups.available else None,
        all_groups_flag.result,
    )
    if targets is None:
        return

    if all_plugins_flag.result:
        if targets:
            messages = []
            for gid in targets:
                messages.append(
                    await PluginManager.set_all_plugin_status(
                        status=status,
                        is_default=default_status.result if is_superuser else False,
                        group_id=gid,
                        is_task=task.result,
                        is_superuser=is_superuser,
                        use_su_field=use_su_field_final,
                    )
                )
            await MessageUtils.build_message("\n".join(messages)).finish(reply_to=True)
        if is_superuser and not session.group:
            result = await PluginManager.set_all_plugin_status(
                status=status,
                is_default=default_status.result,
                group_id=None,
                is_task=task.result,
                is_superuser=is_superuser,
                use_su_field=use_su_field_final,
            )
            await MessageUtils.build_message(result).finish(reply_to=True)
        await MessageUtils.build_message("请输入目标群组").finish(reply_to=True)

    names = plugin_names.result if plugin_names.available else ()
    if isinstance(names, str):
        names = (names,)

    if (
        not targets
        and (not is_superuser or session.group)
        and not default_status.result
        and block_type_val is None
    ):
        await MessageUtils.build_message("请选择一个目标群组").finish(reply_to=True)

    messages = []
    for name in names:
        name_str = str(name)
        if is_superuser and default_status.result:
            result = await PluginManager.set_default_status(
                name_str, status, is_task=task.result
            )
            messages.append(result)
            continue

        if block_type_val is not None:
            _type = BlockType.ALL
            if block_type_val in ["p", "private"]:
                _type = BlockType.PRIVATE
            elif block_type_val in ["g", "group"]:
                _type = BlockType.GROUP
            result = await PluginManager.superuser_set_status(
                name_str, status, _type, None, is_task=task.result
            )
            messages.append(result)
            continue

        if not targets:
            if is_superuser and not session.group:
                target_block_type = None if status else BlockType.ALL
                result = await PluginManager.superuser_set_status(
                    name_str, status, target_block_type, None, is_task=task.result
                )
                messages.append(result)
                continue
            messages.append(f"{name_str}: 请选择一个目标群组")
            continue

        msg = await PluginManager.batch_update_status(
            name_str,
            targets,
            status=status,
            is_task=task.result,
            is_superuser=is_superuser,
            is_whitelist_mode=only_flag_value,
            use_su_field=use_su_field_final,
            bot=bot,
        )
        action_name = "开启" if status else "关闭"
        logger.info(
            f"{action_name}操作: {name_str}, targets={targets}",
            arparma.header_result,
            session=session,
        )
        messages.append(msg)

    await MessageUtils.build_message("\n".join(messages)).finish(reply_to=True)


@_status_matcher.assign("open")
async def _(
    bot: Bot,
    event: Event,
    session: Uninfo,
    arparma: Arparma,
    plugin_names: Match[tuple[str, ...]],
    groups: Match[tuple[str, ...]] = AlconnaMatch("groups"),
    tag: Match[str] = AlconnaMatch("tag"),
    task: Query[bool] = AlconnaQuery("task.value", False),
    default_status: Query[bool] = AlconnaQuery("default.value", False),
    all_groups_flag: Query[bool] = AlconnaQuery("all.value", False),
    all_plugins_flag: Query[bool] = AlconnaQuery("all-plugins.value", False),
    only_flag: Query[bool] = AlconnaQuery("only.value", False),
    use_su_field: Query[bool] = AlconnaQuery("su.value", False),
):
    await _handle_switch_command(
        True,
        bot,
        event,
        session,
        arparma,
        plugin_names,
        groups,
        tag,
        task,
        default_status,
        all_groups_flag,
        all_plugins_flag,
        only_flag=only_flag,
        use_su_field=use_su_field,
    )


@_status_matcher.assign("close")
async def _(
    bot: Bot,
    event: Event,
    session: Uninfo,
    arparma: Arparma,
    plugin_names: Match[tuple[str, ...]],
    groups: Match[tuple[str, ...]] = AlconnaMatch("groups"),
    tag: Match[str] = AlconnaMatch("tag"),
    task: Query[bool] = AlconnaQuery("task.value", False),
    default_status: Query[bool] = AlconnaQuery("default.value", False),
    all_groups_flag: Query[bool] = AlconnaQuery("all.value", False),
    all_plugins_flag: Query[bool] = AlconnaQuery("all-plugins.value", False),
    use_su_field: Query[bool] = AlconnaQuery("su.value", False),
):
    await _handle_switch_command(
        False,
        bot,
        event,
        session,
        arparma,
        plugin_names,
        groups,
        tag,
        task,
        default_status,
        all_groups_flag,
        all_plugins_flag,
        use_su_field=use_su_field,
    )


@_group_status_matcher.handle()
async def _(
    bot: Bot,
    event: Event,
    session: Uninfo,
    arparma: Arparma,
    status: str,
    groups: Match[tuple[str, ...]] = AlconnaMatch("groups"),
    tag: Match[str] = AlconnaMatch("tag"),
    all_flag: Query[bool] = AlconnaQuery("all.value", False),
    only_flag: Query[bool] = AlconnaQuery("only.value", False),
):
    is_wake = status == "wake"

    if status == "check":
        if not await SUPERUSER_PERM(bot, event):
            return

        try:
            image = await render_group_active_status(bot)
            logger.info(
                "查看全服群组工作状态报表", arparma.header_result, session=session
            )
            await MessageUtils.build_message(image).finish(reply_to=True)
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"渲染群组激活状态报表失败: {e}", e=e)
            await MessageUtils.build_message("生成状态报表失败，请检查日志").finish(
                reply_to=True
            )
        return

    targets = await get_target_groups(
        bot,
        event,
        session,
        tag.result if tag and tag.available else None,
        groups.result if groups and groups.available else None,
        all_flag.result,
    )

    if not targets:
        await MessageUtils.build_message("请指定目标群组或在群聊中使用").finish(
            reply_to=True
        )
        return

    msg = await PluginManager.batch_set_group_active_status(
        targets, status=is_wake, is_whitelist_mode=only_flag.result, bot=bot
    )

    action_name = "醒来" if is_wake else "进行休眠"
    reply_msg = "呜..醒来了..." if is_wake else "那我先睡觉了..."
    if len(targets) > 1 or only_flag.result:
        reply_msg = msg

    logger.info(action_name, arparma.header_result, session=session)
    await MessageUtils.build_message(reply_msg).finish(reply_to=True)


@_status_matcher.assign("task")
async def _(
    session: Uninfo,
    arparma: Arparma,
):
    if arparma.find("check") or arparma.find("open") or arparma.find("close"):
        return

    image = await build_task(session.group.id if session.group else None)
    if image:
        logger.info("查看群被动列表", arparma.header_result, session=session)
        await MessageUtils.build_message(image).finish(reply_to=True)
    else:
        await MessageUtils.build_message("获取群被动任务失败...").finish(reply_to=True)


@_status_matcher.assign("check")
async def _(
    bot: Bot,
    event: Event,
    plugin_name: Match[str],
    task: Query[bool] = AlconnaQuery("task.value", False),
):
    if not await SUPERUSER_PERM(bot, event):
        return

    name = plugin_name.result
    try:
        img = await render_global_status(name, is_task=task.result, bot=bot)
        await MessageUtils.build_message(img).finish(reply_to=True)
    except FinishedException:
        raise
    except ValueError as e:
        await MessageUtils.build_message(str(e)).finish(reply_to=True)
    except Exception as e:
        logger.error(f"渲染状态图表失败: {e}", e=e)
        await MessageUtils.build_message("生成状态报表失败，请检查日志").finish(
            reply_to=True
        )

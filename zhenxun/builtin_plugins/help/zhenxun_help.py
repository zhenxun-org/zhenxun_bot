import nonebot
from nonebot_plugin_htmlrender import template_to_pic
from nonebot_plugin_uninfo import Uninfo
from pydantic import BaseModel

from zhenxun.configs.config import BotConfig
from zhenxun.configs.path_config import TEMPLATE_PATH
from zhenxun.configs.utils import PluginExtraData
from zhenxun.models.bot_console import BotConsole
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.utils.enum import BlockType
from zhenxun.utils.platform import PlatformUtils

from ._utils import classify_plugin


class Item(BaseModel):
    plugin_name: str
    """插件名称"""
    commands: list[str]
    """插件命令"""
    id: str
    """插件id"""
    status: bool
    """插件状态"""
    has_superuser_help: bool
    """插件是否拥有超级用户帮助"""


def __handle_item(
    bot: BotConsole | None,
    plugin: PluginInfo,
    group: GroupConsole | None,
    is_detail: bool,
):
    """构造Item

    参数:
        bot: BotConsole
        plugin: PluginInfo
        group: 群组
        is_detail: 是否为详细

    返回:
        Item: Item
    """
    status = True
    has_superuser_help = False
    nb_plugin = nonebot.get_plugin_by_module_name(plugin.module_path)
    if nb_plugin and nb_plugin.metadata and nb_plugin.metadata.extra:
        extra_data = PluginExtraData(**nb_plugin.metadata.extra)
        if extra_data.superuser_help:
            has_superuser_help = True
    if not plugin.status:
        if plugin.block_type == BlockType.ALL:
            status = False
        elif group and plugin.block_type == BlockType.GROUP:
            status = False
        elif not group and plugin.block_type == BlockType.PRIVATE:
            status = False
    elif group and f"{plugin.module}," in group.block_plugin:
        status = False
    elif bot and f"{plugin.module}," in bot.block_plugins:
        status = False
    commands = []
    nb_plugin = nonebot.get_plugin_by_module_name(plugin.module_path)
    if is_detail and nb_plugin and nb_plugin.metadata and nb_plugin.metadata.extra:
        extra_data = PluginExtraData(**nb_plugin.metadata.extra)
        commands = [cmd.command for cmd in extra_data.commands]
    return Item(
        plugin_name=plugin.name,
        commands=commands,
        id=str(plugin.id),
        status=status,
        has_superuser_help=has_superuser_help,
    )


def build_plugin_data(classify: dict[str, list[Item]]) -> list[dict[str, str]]:
    """构建前端插件数据

    参数:
        classify: 插件数据

    返回:
        list[dict[str, str]]: 前端插件数据
    """
    classify = dict(sorted(classify.items(), key=lambda x: len(x[1]), reverse=True))
    menu_key = next(iter(classify.keys()))
    max_data = classify[menu_key]
    del classify[menu_key]
    plugin_list = [
        {
            "name": "主要功能" if menu in ["normal", "功能"] else menu,
            "items": value,
        }
        for menu, value in classify.items()
    ]
    plugin_list.insert(0, {"name": menu_key, "items": max_data})
    for plugin in plugin_list:
        plugin["items"].sort(key=lambda x: x.id)
    return plugin_list


async def build_zhenxun_image(
    session: Uninfo, group_id: str | None, is_detail: bool
) -> bytes:
    """构造真寻帮助图片

    参数:
        bot_id: bot_id
        group_id: 群号
        is_detail: 是否详细帮助
    """
    classify = await classify_plugin(session, group_id, is_detail, __handle_item)
    plugin_list = build_plugin_data(classify)
    platform = PlatformUtils.get_platform(session)
    bot_id = BotConfig.get_qbot_uid(session.self_id) or session.self_id
    bot_ava = PlatformUtils.get_user_avatar_url(bot_id, platform)
    width = int(637 * 1.5) if is_detail else 637
    title_font = int(53 * 1.5) if is_detail else 53
    tip_font = int(19 * 1.5) if is_detail else 19
    plugin_count = sum(len(plugin["items"]) for plugin in plugin_list)
    return await template_to_pic(
        template_path=str((TEMPLATE_PATH / "ss_menu").absolute()),
        template_name="main.html",
        templates={
            "data": {
                "plugin_list": plugin_list,
                "ava": bot_ava,
                "width": width,
                "font_size": (title_font, tip_font),
                "is_detail": is_detail,
                "plugin_count": plugin_count,
            }
        },
        pages={
            "viewport": {"width": width, "height": 10},
            "base_url": f"file://{TEMPLATE_PATH}",
        },
        wait=2,
    )

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
    if not plugin.status:
        if plugin.block_type == BlockType.ALL:
            plugin.name = f"{plugin.name}(不可用)"
        elif group and plugin.block_type == BlockType.GROUP:
            plugin.name = f"{plugin.name}(不可用)"
        elif not group and plugin.block_type == BlockType.PRIVATE:
            plugin.name = f"{plugin.name}(不可用)"
    elif group and f"{plugin.module}," in group.block_plugin:
        plugin.name = f"{plugin.name}(不可用)"
    elif bot and f"{plugin.module}," in bot.block_plugins:
        plugin.name = f"{plugin.name}(不可用)"
    commands = []
    nb_plugin = nonebot.get_plugin_by_module_name(plugin.module_path)
    if is_detail and nb_plugin and nb_plugin.metadata and nb_plugin.metadata.extra:
        extra_data = PluginExtraData(**nb_plugin.metadata.extra)
        commands = [cmd.command for cmd in extra_data.commands]
    return Item(plugin_name=f"{plugin.id}-{plugin.name}", commands=commands)


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
    plugin_list = build_line_data(plugin_list)
    plugin_list.insert(
        0,
        build_plugin_line(
            menu_key if menu_key not in ["normal", "功能"] else "主要功能",
            max_data,
            30,
            100,
            True,
        ),
    )
    return plugin_list


def build_plugin_line(
    name: str, items: list, left: int, width: int | None = None, is_max: bool = False
) -> dict:
    """构造插件行数据

    参数:
        name: 菜单名称
        items: 插件名称列表
        left: 左边距
        width: 总插件长度.
        is_max: 是否为最大长度的插件菜单

    返回:
        dict: 插件数据
    """
    _plugins = []
    width = width or 50
    if len(items) // 2 > 6 or is_max:
        width = 100
        plugin_list1 = []
        plugin_list2 = []
        for i in range(len(items)):
            if i % 2:
                plugin_list1.append(items[i])
            else:
                plugin_list2.append(items[i])
        _plugins = [(30, 50, plugin_list1), (0, 50, plugin_list2)]
    else:
        _plugins = [(left, 100, items)]
    return {"name": name, "items": _plugins, "width": width}


def build_line_data(plugin_list: list[dict]) -> list[dict]:
    """构造插件数据

    参数:
        plugin_list: 插件列表

    返回:
        list[dict]: 插件数据
    """
    left = 30
    data = []
    for plugin in plugin_list:
        data.append(build_plugin_line(plugin["name"], plugin["items"], left))
        if len(plugin["items"]) // 2 <= 6:
            left = 15 if left == 30 else 30
    return data


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
            }
        },
        pages={
            "viewport": {"width": width, "height": 453},
            "base_url": f"file://{TEMPLATE_PATH}",
        },
        wait=2,
    )

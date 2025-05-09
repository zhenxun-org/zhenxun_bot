import os
import random

from nonebot_plugin_htmlrender import template_to_pic
from nonebot_plugin_uninfo import Uninfo
from pydantic import BaseModel

from zhenxun.configs.path_config import TEMPLATE_PATH
from zhenxun.models.bot_console import BotConsole
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.utils.enum import BlockType

from ._utils import classify_plugin

LOGO_PATH = TEMPLATE_PATH / "menu" / "res" / "logo"


class Item(BaseModel):
    plugin_name: str
    """插件名称"""
    sta: int
    """插件状态"""


class PluginList(BaseModel):
    plugin_type: str
    """菜单名称"""
    icon: str
    """图标"""
    logo: str
    """logo"""
    items: list[Item]
    """插件列表"""


ICON2STR = {
    "normal": "fa fa-cog",
    "原神相关": "fa fa-circle-o",
    "常规插件": "fa fa-cubes",
    "联系管理员": "fa fa-envelope-o",
    "抽卡相关": "fa fa-credit-card-alt",
    "来点好康的": "fa fa-picture-o",
    "数据统计": "fa fa-bar-chart",
    "一些工具": "fa fa-shopping-cart",
    "商店": "fa fa-shopping-cart",
    "其它": "fa fa-tags",
    "群内小游戏": "fa fa-gamepad",
}


def __handle_item(
    bot: BotConsole, plugin: PluginInfo, group: GroupConsole | None, is_detail: bool
) -> Item:
    """构造Item

    参数:
        bot: BotConsole
        plugin: PluginInfo
        group: 群组
        is_detail: 是否详细

    返回:
        Item: Item
    """
    sta = 0
    if not plugin.status:
        if group and plugin.block_type in [
            BlockType.ALL,
            BlockType.GROUP,
        ]:
            sta = 2
        if not group and plugin.block_type in [
            BlockType.ALL,
            BlockType.PRIVATE,
        ]:
            sta = 2
    if group:
        if f"{plugin.module}," in group.superuser_block_plugin:
            sta = 2
        if f"{plugin.module}," in group.block_plugin:
            sta = 1
    if bot:
        if f"{plugin.module}," in bot.block_plugins:
            sta = 2
    return Item(plugin_name=plugin.name, sta=sta)


def build_plugin_data(classify: dict[str, list[Item]]) -> list[dict[str, str]]:
    """构建前端插件数据

    参数:
        classify: 插件数据

    返回:
        list[dict[str, str]]: 前端插件数据
    """
    lengths = [len(classify[c]) for c in classify]
    index = lengths.index(max(lengths))
    menu_key = list(classify.keys())[index]
    max_data = classify[menu_key]
    del classify[menu_key]
    plugin_list = []
    for menu_type in classify:
        icon = "fa fa-pencil-square-o"
        if menu_type in ICON2STR.keys():
            icon = ICON2STR[menu_type]
        logo = LOGO_PATH / random.choice(os.listdir(LOGO_PATH))
        data = {
            "name": menu_type if menu_type != "normal" else "功能",
            "items": classify[menu_type],
            "icon": icon,
            "logo": str(logo.absolute()),
        }
        plugin_list.append(data)
    plugin_list.insert(
        0,
        {
            "name": menu_key if menu_key != "normal" else "功能",
            "items": max_data,
            "icon": "fa fa-pencil-square-o",
            "logo": str((LOGO_PATH / random.choice(os.listdir(LOGO_PATH))).absolute()),
        },
    )
    return plugin_list


async def build_html_image(
    session: Uninfo, group_id: str | None, is_detail: bool
) -> bytes:
    """构造HTML帮助图片

    参数:
        session: Uninfo
        group_id: 群号
        is_detail: 是否详细帮助
    """
    classify = await classify_plugin(session, group_id, is_detail, __handle_item)
    plugin_list = build_plugin_data(classify)
    return await template_to_pic(
        template_path=str((TEMPLATE_PATH / "menu").absolute()),
        template_name="zhenxun_menu.html",
        templates={"plugin_list": plugin_list},
        pages={
            "viewport": {"width": 1903, "height": 975},
            "base_url": f"file://{TEMPLATE_PATH}",
        },
        wait=2,
    )

from collections import defaultdict

import nonebot
from nonebot.plugin import PluginMetadata
from pydantic import BaseModel

from zhenxun import ui
from zhenxun.configs.config import BotConfig
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.models.task_info import TaskInfo
from zhenxun.ui.models import HelpCategory, HelpItem, PluginHelpPageData
from zhenxun.utils.common_utils import format_usage_for_markdown
from zhenxun.utils.enum import PluginType


class PluginData(BaseModel):
    plugin: PluginInfo
    metadata: PluginMetadata

    class Config:
        arbitrary_types_allowed = True


async def _get_plugins_by_types(plugin_types: list[PluginType]) -> list[PluginData]:
    """根据指定的插件类型列表获取插件数据"""
    plugin_list = await PluginInfo.filter(plugin_type__in=plugin_types).all()
    data_list = []
    for plugin in plugin_list:
        if _plugin := nonebot.get_plugin_by_module_name(plugin.module_path):
            if _plugin.metadata:
                data_list.append(PluginData(plugin=plugin, metadata=_plugin.metadata))
    return data_list


async def _get_task_category() -> dict:
    """获取被动技能帮助类别"""
    task_items = []
    if task_list := await TaskInfo.all():
        task_names = "\n".join([task.name for task in task_list])
        task_items.append(
            {
                "name": "被动技能",
                "description": "控制群组中的被动技能状态",
                "usage": "通过 开启/关闭群被动 来控制群被动\n"
                + " 示例：开启/关闭群被动早晚安\n 示例：开启/关闭全部群被动"
                + " \n ---------- \n "
                + task_names,
            }
        )

    return {
        "title": "被动技能管理",
        "icon_svg_path": "M10,20V14H14V20H19V12H22L12,3L2,12H5V20H10Z",
        "items": task_items,
    }


async def create_plugin_help_image(
    plugin_types: list[PluginType], page_title: str
) -> bytes:
    """
    一个通用的函数，用于创建插件帮助图片。

    参数:
        plugin_types: 要包含在帮助中的插件类型列表。
        page_title: 生成图片的标题。

    返回:
        bytes: 生成的图片字节流。
    """
    plugins_data = await _get_plugins_by_types(plugin_types)

    grouped_plugins = defaultdict(list)
    for data in plugins_data:
        menu_type = data.plugin.menu_type or "功能"
        grouped_plugins[menu_type].append(
            HelpItem(
                name=data.plugin.name,
                description=format_usage_for_markdown(data.metadata.description),
                usage=format_usage_for_markdown(data.metadata.usage),
            )
        )

    # 直接构建 HelpCategory 列表
    categories = []

    for menu_type, items in grouped_plugins.items():
        categories.append(
            HelpCategory(
                title=menu_type,
                icon_svg_path="M12,2L15.09,8.26L22,9.27L17,14.14L18.18,21.02L12,17.77L5.82,21.02L7,14.14L2,9.27L8.91,8.26L12,2Z",
                items=sorted(items, key=lambda x: x.name),
            )
        )

    task_category_data = await _get_task_category()
    if task_category_data["items"]:
        task_items = [HelpItem(**item) for item in task_category_data["items"]]
        categories.append(
            HelpCategory(
                title=task_category_data["title"],
                icon_svg_path=task_category_data["icon_svg_path"],
                items=task_items,
            )
        )

    # 直接实例化 Data Model
    page_data = PluginHelpPageData(
        bot_nickname=BotConfig.self_nickname,
        page_title=page_title,
        categories=categories,
    )

    image_bytes = await ui.render(page_data, use_cache=True)

    return image_bytes

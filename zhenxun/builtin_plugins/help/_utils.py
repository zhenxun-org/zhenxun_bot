from collections.abc import Callable

from nonebot_plugin_uninfo import Uninfo

from zhenxun.models.bot_console import BotConsole
from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.utils.enum import PluginType


async def sort_type() -> dict[str, list[PluginInfo]]:
    """
    对插件按照菜单类型分类
    """
    data = await PluginInfo.filter(
        menu_type__not="",
        load_status=True,
        plugin_type__in=[PluginType.NORMAL, PluginType.DEPENDANT],
        is_show=True,
    )
    sort_data = {}
    for plugin in data:
        menu_type = plugin.menu_type or "normal"
        if menu_type == "normal":
            menu_type = "功能"
        if not sort_data.get(menu_type):
            sort_data[menu_type] = []
        sort_data[menu_type].append(plugin)
    return sort_data


async def classify_plugin(
    session: Uninfo, group_id: str | None, is_detail: bool, handle: Callable
) -> dict[str, list]:
    """对插件进行分类并判断状态

    参数:
        session: Uninfo对象
        group_id: 群组id
        is_detail: 是否详细帮助
        handle: 回调方法

    返回:
        dict[str, list[Item]]: 分类插件数据
    """
    sort_data = await sort_type()
    classify: dict[str, list] = {}
    group = await GroupConsole.get_or_none(group_id=group_id) if group_id else None
    bot = await BotConsole.get_or_none(bot_id=session.self_id)
    for menu, value in sort_data.items():
        for plugin in value:
            if not classify.get(menu):
                classify[menu] = []
            classify[menu].append(handle(bot, plugin, group, is_detail))
    return classify

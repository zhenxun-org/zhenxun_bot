from nonebot_plugin_alconna import AlconnaMatch, Match
from nonebot_plugin_uninfo import Uninfo

from zhenxun.builtin_plugins.superuser.bot_manage.command import bot_manage
from zhenxun.models.bot_console import BotConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.log import logger
from zhenxun.utils._build_image import BuildImage
from zhenxun.utils._image_template import ImageTemplate, RowStyle
from zhenxun.utils.enum import PluginType
from zhenxun.utils.message import MessageUtils


def task_row_style(column: str, text: str) -> RowStyle:
    """被动技能文本风格

    参数:
        column: 表头
        text: 文本内容

    返回:
        RowStyle: RowStyle
    """
    style = RowStyle()
    if column in {"全局状态"}:
        style.font_color = "#67C23A" if text == "开启" else "#F56C6C"
    return style


@bot_manage.assign("plugin.list")
async def bot_plugin(session: Uninfo, bot_id: Match[str] = AlconnaMatch("bot_id")):
    logger.info("获取全部 bot 的所有可用插件", "bot_manage.plugin", session=session)
    column_name = [
        "ID",
        "模块",
        "名称",
        "全局状态",
        "禁用类型",
        "加载状态",
        "菜单分类",
        "作者",
        "版本",
        "金币花费",
    ]
    if bot_id.available:
        data_dict = {
            bot_id.result: await BotConsole.get_plugins(
                bot_id=bot_id.result, status=False
            )
        }
    else:
        data_dict = await BotConsole.get_plugins(status=False)
    db_plugin_list = await PluginInfo.filter(
        load_status=True, plugin_type__not=PluginType.HIDDEN
    ).all()
    img_list = []
    for __bot_id, tk in data_dict.items():
        column_data = [
            [
                plugin.id,
                plugin.module,
                plugin.name,
                "开启" if plugin.module not in tk else "关闭",
                plugin.block_type,
                "SUCCESS" if plugin.load_status else "ERROR",
                plugin.menu_type,
                plugin.author,
                plugin.version,
                plugin.cost_gold,
            ]
            for plugin in db_plugin_list
        ]
        img = await ImageTemplate.table_page(
            f"{__bot_id}插件列表",
            None,
            column_name,
            column_data,
            text_style=task_row_style,
        )
        img_list.append(img)
    result = await BuildImage.auto_paste(img_list, 3)
    await MessageUtils.build_message(result).finish()


@bot_manage.assign("plugin.enable")
async def enable_plugin(
    session: Uninfo,
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    bot_id: Match[str] = AlconnaMatch("bot_id"),
):
    if plugin_name.available:
        plugin: PluginInfo | None = await PluginInfo.get_plugin(name=plugin_name.result)
        if not plugin:
            await MessageUtils.build_message("未找到该插件...").finish()
        if bot_id.available:
            logger.info(
                f"开启 {bot_id.result} 的插件 {plugin_name.result}",
                "bot_manage.plugin.disable",
                session=session,
            )
            await BotConsole.enable_plugin(bot_id.result, plugin.module)
            await MessageUtils.build_message(
                f"已开启 {bot_id.result} 的插件 {plugin_name.result}"
            ).finish()
        else:
            logger.info(
                f"开启全部 bot 的插件: {plugin_name.result}",
                "bot_manage.plugin.disable",
                session=session,
            )
            await BotConsole.enable_plugin(None, plugin.module)
            await MessageUtils.build_message(
                f"已开启全部 bot 的插件: {plugin_name.result}"
            ).finish()
    elif bot_id.available:
        logger.info(
            f"开启 {bot_id.result} 全部插件",
            "bot_manage.plugin.disable",
            session=session,
        )
        await BotConsole.enable_all(bot_id.result, "plugins")
        await MessageUtils.build_message(f"已开启 {bot_id.result} 全部插件").finish()
    else:
        bot_id_list = await BotConsole.annotate().values_list("bot_id", flat=True)
        for __bot_id in bot_id_list:
            await BotConsole.enable_all(__bot_id, "plugins")  # type: ignore
        logger.info(
            "开启全部 bot 全部插件",
            "bot_manage.plugin.disable",
            session=session,
        )
        await MessageUtils.build_message("开启全部 bot 全部插件").finish()


@bot_manage.assign("plugin.disable")
async def disable_plugin(
    session: Uninfo,
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    bot_id: Match[str] = AlconnaMatch("bot_id"),
):
    if plugin_name.available:
        plugin = await PluginInfo.get_plugin(name=plugin_name.result)
        if not plugin:
            await MessageUtils.build_message("未找到该插件...").finish()
        if bot_id.available:
            logger.info(
                f"禁用 {bot_id.result} 的插件 {plugin_name.result}",
                "bot_manage.plugin.disable",
                session=session,
            )
            await BotConsole.disable_plugin(bot_id.result, plugin.module)
            await MessageUtils.build_message(
                f"已禁用 {bot_id.result} 的插件 {plugin_name.result}"
            ).finish()
        else:
            logger.info(
                f"禁用全部 bot 的插件: {plugin_name.result}",
                "bot_manage.plugin.disable",
                session=session,
            )
            await BotConsole.disable_plugin(None, plugin.module)
            await MessageUtils.build_message(
                f"已禁用全部 bot 的插件: {plugin_name.result}"
            ).finish()
    elif bot_id.available:
        logger.info(
            f"禁用 {bot_id.result} 全部插件",
            "bot_manage.plugin.disable",
            session=session,
        )
        await BotConsole.disable_all(bot_id.result, "plugins")
        await MessageUtils.build_message(f"已禁用 {bot_id.result} 全部插件").finish()
    else:
        bot_id_list = await BotConsole.annotate().values_list("bot_id", flat=True)
        for __bot_id in bot_id_list:
            await BotConsole.disable_all(__bot_id, "plugins")  # type: ignore
        logger.info(
            "禁用全部 bot 全部插件",
            "bot_manage.plugin.disable",
            session=session,
        )
        await MessageUtils.build_message("禁用全部 bot 全部插件").finish()

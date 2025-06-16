from nonebot_plugin_alconna import AlconnaMatch, Match
from nonebot_plugin_uninfo import Uninfo

from zhenxun.builtin_plugins.superuser.bot_manage.command import bot_manage
from zhenxun.models.bot_console import BotConsole
from zhenxun.models.task_info import TaskInfo
from zhenxun.services.log import logger
from zhenxun.utils._build_image import BuildImage
from zhenxun.utils._image_template import RowStyle
from zhenxun.utils.image_utils import ImageTemplate
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


@bot_manage.assign("task.list")
async def bot_task(session: Uninfo, bot_id: Match[str] = AlconnaMatch("bot_id")):
    logger.info("获取全部 bot 的所有可用被动", "bot_manage.task", session=session)
    if bot_id.available:
        data_dict = {
            bot_id.result: await BotConsole.get_tasks(
                bot_id=bot_id.result, status=False
            )
        }
    else:
        data_dict = await BotConsole.get_tasks(status=False)
    db_task_list = await TaskInfo.all()
    column_name = ["ID", "模块", "名称", "全局状态", "运行时间"]
    img_list = []
    for __bot_id, tk in data_dict.items():
        column_data = [
            [
                task.id,
                task.module,
                task.name,
                "开启" if task.module not in tk else "关闭",
                task.run_time or "-",
            ]
            for task in db_task_list
        ]
        img = await ImageTemplate.table_page(
            f"{__bot_id}被动技能状态",
            None,
            column_name,
            column_data,
            text_style=task_row_style,
        )
        img_list.append(img)
    result = await BuildImage.auto_paste(img_list, 3)
    await MessageUtils.build_message(result).finish()


@bot_manage.assign("task.enable")
async def enable_task(
    session: Uninfo,
    task_name: Match[str] = AlconnaMatch("feature_name"),
    bot_id: Match[str] = AlconnaMatch("bot_id"),
):
    if task_name.available:
        task: TaskInfo | None = await TaskInfo.get_or_none(name=task_name.result)
        if not task:
            await MessageUtils.build_message("未找到被动...").finish()
        if bot_id.available:
            logger.info(
                f"开启 {bot_id.result} 被动的 {task_name.available}",
                "bot_manage.task.disable",
                session=session,
            )
            await BotConsole.enable_task(bot_id.result, task.module)
            await MessageUtils.build_message(
                f"已开启 {bot_id.result} 被动的 {task_name.available}"
            ).finish()
        else:
            logger.info(
                f"开启全部 bot 的被动: {task_name.available}",
                "bot_manage.task.disable",
                session=session,
            )
            await BotConsole.enable_task(None, task.module)
            await MessageUtils.build_message(
                f"已开启全部 bot 的被动: {task_name.available}"
            ).finish()
    elif bot_id.available:
        logger.info(
            f"开启 {bot_id.result} 全部被动",
            "bot_manage.task.disable",
            session=session,
        )
        await BotConsole.enable_all(bot_id.result, "tasks")
        await MessageUtils.build_message(f"已开启 {bot_id.result} 全部被动").finish()
    else:
        bot_id_list = await BotConsole.annotate().values_list("bot_id", flat=True)
        for __bot_id in bot_id_list:
            await BotConsole.enable_all(__bot_id, "tasks")  # type: ignore
        logger.info(
            "开启全部 bot 全部被动",
            "bot_manage.task.disable",
            session=session,
        )
        await MessageUtils.build_message("开启全部 bot 全部被动").finish()


@bot_manage.assign("task.disable")
async def disable_task(
    session: Uninfo,
    task_name: Match[str] = AlconnaMatch("feature_name"),
    bot_id: Match[str] = AlconnaMatch("bot_id"),
):
    if task_name.available:
        task: TaskInfo | None = await TaskInfo.get_or_none(name=task_name.result)
        if not task:
            await MessageUtils.build_message("未找到被动...").finish()
        if bot_id.available:
            logger.info(
                f"禁用 {bot_id.result} 被动的 {task_name.available}",
                "bot_manage.task.disable",
                session=session,
            )
            await BotConsole.disable_task(bot_id.result, task.module)
            await MessageUtils.build_message(
                f"已禁用 {bot_id.result} 被动的 {task_name.available}"
            ).finish()
        else:
            logger.info(
                f"禁用全部 bot 的被动: {task_name.available}",
                "bot_manage.task.disable",
                session=session,
            )
            await BotConsole.disable_task(None, task.module)
            await MessageUtils.build_message(
                f"已禁用全部 bot 的被动: {task_name.available}"
            ).finish()
    elif bot_id.available:
        logger.info(
            f"禁用 {bot_id.result} 全部被动",
            "bot_manage.task.disable",
            session=session,
        )
        await BotConsole.disable_all(bot_id.result, "tasks")
        await MessageUtils.build_message(f"已禁用 {bot_id.result} 全部被动").finish()
    else:
        bot_id_list = await BotConsole.annotate().values_list("bot_id", flat=True)
        for __bot_id in bot_id_list:
            await BotConsole.disable_all(__bot_id, "tasks")  # type: ignore
        logger.info(
            "禁用全部 bot 全部被动",
            "bot_manage.task.disable",
            session=session,
        )
        await MessageUtils.build_message("禁用全部 bot 全部被动").finish()

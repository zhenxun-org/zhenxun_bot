import asyncio

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna import (
    Alconna,
    AlconnaMatch,
    Args,
    Match,
    Option,
    Query,
    Subcommand,
    on_alconna,
)

from zhenxun.utils._image_template import ImageTemplate
from zhenxun.utils.manager.schedule_manager import scheduler_manager
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.rules import admin_check, ensure_group


def _format_trigger(schedule_status: dict) -> str:
    """将触发器配置格式化为人类可读的字符串"""
    trigger_type = schedule_status["trigger_type"]
    config = schedule_status["trigger_config"]

    if trigger_type == "cron":
        hour = config.get("hour")
        minute = config.get("minute")
        hour_str = f"{hour:02d}" if hour is not None else "*"
        minute_str = f"{minute:02d}" if minute is not None else "*"
        trigger_str = f"每天 {hour_str}:{minute_str}"
    elif trigger_type == "interval":
        seconds = config.get("seconds", 0)
        minutes = config.get("minutes", 0)
        hours = config.get("hours", 0)
        if hours:
            trigger_str = f"每 {hours} 小时"
        elif minutes:
            trigger_str = f"每 {minutes} 分钟"
        else:
            trigger_str = f"每 {seconds} 秒"
    elif trigger_type == "date":
        run_date = config.get("run_date", "未知时间")
        trigger_str = f"在 {run_date}"
    else:
        trigger_str = f"{trigger_type}: {config}"

    return trigger_str


def _format_params(schedule_status: dict) -> str:
    """将任务参数格式化为人类可读的字符串"""
    if kwargs := schedule_status.get("job_kwargs"):
        kwargs_str = " | ".join(f"{k}: {v}" for k, v in kwargs.items())
        return kwargs_str
    return "-"


schedule_cmd = on_alconna(
    Alconna(
        "定时任务",
        Subcommand(
            "查看",
            Option("-g", Args["target_group_id", str]),
            Option("-all", help_text="查看所有群聊 (SUPERUSER)"),
            Option("-p", Args["plugin_name", str], help_text="按插件名筛选"),
            Option("--page", Args["page", int, 1], help_text="指定页码"),
            alias=["ls", "list"],
            help_text="查看定时任务",
        ),
        Subcommand(
            "设置",
            Args["plugin_name", str]["time", str],
            Option("-g", Args["group_id", str], help_text="指定群组ID"),
            Option("-all", help_text="对所有群生效"),
            Option("--kwargs", Args["kwargs_str", str], help_text="设置任务参数"),
            alias=["add", "开启"],
            help_text="设置/开启一个定时任务",
        ),
        Subcommand(
            "删除",
            Args["schedule_id?", int],
            Option("-p", Args["plugin_name", str], help_text="指定插件名"),
            Option("-g", Args["group_id", str], help_text="指定群组ID"),
            Option("-all", help_text="对所有群生效"),
            alias=["del", "rm", "remove", "关闭", "取消"],
            help_text="删除一个或多个定时任务",
        ),
        Subcommand(
            "暂停",
            Args["schedule_id?", int],
            Option("-all", help_text="对当前群所有任务生效"),
            Option("-p", Args["plugin_name", str], help_text="指定插件名"),
            Option("-g", Args["group_id", str], help_text="指定群组ID (SUPERUSER)"),
            alias=["pause"],
            help_text="暂停一个或多个定时任务",
        ),
        Subcommand(
            "恢复",
            Args["schedule_id?", int],
            Option("-all", help_text="对当前群所有任务生效"),
            Option("-p", Args["plugin_name", str], help_text="指定插件名"),
            Option("-g", Args["group_id", str], help_text="指定群组ID (SUPERUSER)"),
            alias=["resume"],
            help_text="恢复一个或多个定时任务",
        ),
        Subcommand(
            "执行",
            Args["schedule_id", int],
            alias=["trigger", "run"],
            help_text="立即执行一次任务",
        ),
        Subcommand(
            "更新",
            Args["schedule_id", int],
            Option("--time", Args["time", str], help_text="更新时间 (HH:MM)"),
            Option("--kwargs", Args["kwargs_str", str], help_text="更新参数"),
            alias=["update", "modify", "修改"],
            help_text="更新任务配置",
        ),
        Subcommand(
            "插件列表",
            alias=["plugins"],
            help_text="列出所有可用的插件",
        ),
    ),
    priority=5,
    block=True,
    rule=admin_check(1) & ensure_group,
)


@schedule_cmd.assign("查看")
async def _(
    bot: Bot,
    event: GroupMessageEvent,
    target_group_id: Match[str] = AlconnaMatch("target_group_id"),
    all_groups: Query[bool] = Query("查看.all"),
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    page: Match[int] = AlconnaMatch("page"),
):
    is_superuser = await SUPERUSER(bot, event)
    schedules = []
    title = ""

    if all_groups.available:
        if not is_superuser:
            await schedule_cmd.finish("需要超级用户权限才能查看所有群组的定时任务。")
        schedules = await scheduler_manager.get_all_schedules()
        title = "所有群组的定时任务"
    elif target_group_id.available:
        if not is_superuser:
            await schedule_cmd.finish("需要超级用户权限才能查看指定群组的定时任务。")
        gid = target_group_id.result
        schedules = [
            s for s in await scheduler_manager.get_all_schedules() if s.group_id == gid
        ]
        title = f"群 {gid} 的定时任务"
    else:
        gid = str(event.group_id)
        schedules = [
            s for s in await scheduler_manager.get_all_schedules() if s.group_id == gid
        ]
        title = "本群的定时任务"

    if plugin_name.available:
        schedules = [s for s in schedules if s.plugin_name == plugin_name.result]
        title += f" [插件: {plugin_name.result}]"

    if not schedules:
        await schedule_cmd.finish("没有找到任何相关的定时任务。")

    page_size = 15
    current_page = page.result
    total_items = len(schedules)
    total_pages = (total_items + page_size - 1) // page_size
    start_index = (current_page - 1) * page_size
    end_index = start_index + page_size
    paginated_schedules = schedules[start_index:end_index]

    if not paginated_schedules:
        await schedule_cmd.finish("这一页没有内容了哦~")

    status_tasks = [
        scheduler_manager.get_schedule_status(s.id) for s in paginated_schedules
    ]
    all_statuses = await asyncio.gather(*status_tasks)
    data_list = [
        [
            s["id"],
            s["plugin_name"],
            s["group_id"],
            s["next_run_time"],
            _format_trigger(s),
            _format_params(s),
            "✔️ 已启用" if s["is_enabled"] else "⏸️ 已暂停",
        ]
        for s in all_statuses
        if s
    ]

    if not data_list:
        await schedule_cmd.finish("没有找到任何相关的定时任务。")

    img = await ImageTemplate.table_page(
        head_text=title,
        tip_text=f"第 {current_page}/{total_pages} 页，共 {total_items} 条任务",
        column_name=["ID", "插件", "群组/目标", "下次运行", "触发规则", "参数", "状态"],
        data_list=data_list,
        column_space=20,
    )
    await MessageUtils.build_message(img).send(reply_to=True)


@schedule_cmd.assign("设置")
async def _(
    plugin_name: str,
    time: str,
    group_id: Match[str] = AlconnaMatch("group_id"),
    kwargs_str: Match[str] = AlconnaMatch("kwargs_str"),
    all_enabled: Query[bool] = Query("设置.all"),
):
    if plugin_name not in scheduler_manager._registered_tasks:
        await schedule_cmd.finish(
            f"插件 '{plugin_name}' 没有注册可用的定时任务。\n"
            f"可用插件: {list(scheduler_manager._registered_tasks.keys())}"
        )
    try:
        time_parts = time.split(":")
        if len(time_parts) != 2:
            raise ValueError("时间格式应为 HH:MM")
        hour, minute = map(int, time_parts)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("小时或分钟超出有效范围")
        trigger_config = {"hour": hour, "minute": minute}
    except ValueError as e:
        await schedule_cmd.finish(f"时间格式错误: {e}")

    job_kwargs = None
    if kwargs_str.available:
        task_meta = scheduler_manager._registered_tasks[plugin_name]
        if not task_meta.get("params"):
            await schedule_cmd.finish(f"插件 '{plugin_name}' 不支持设置额外参数。")

        registered_params = task_meta["params"]
        job_kwargs = {}
        try:
            for item in kwargs_str.result.split(","):
                key, value = item.strip().split("=", 1)
                key = key.strip()
                if key not in registered_params:
                    await schedule_cmd.finish(f"错误：插件不支持参数 '{key}'。")
                param_type = registered_params[key].get("type", str)
                job_kwargs[key] = param_type(value)
        except Exception as e:
            await schedule_cmd.finish(
                f"参数格式错误，请使用 'key=value,key2=value2' 格式。错误: {e}"
            )

    if all_enabled.available:
        success, fail = await scheduler_manager.add_schedule_for_all(
            plugin_name, "cron", trigger_config, job_kwargs
        )
        await schedule_cmd.finish(f"已为 {success} 个群组设置任务，{fail} 个失败。")
    elif group_id.available:
        success, msg = await scheduler_manager.add_schedule(
            plugin_name, group_id.result, "cron", trigger_config, job_kwargs
        )
        await schedule_cmd.finish(msg)
    else:
        success, msg = await scheduler_manager.add_schedule(
            plugin_name, None, "cron", trigger_config, job_kwargs
        )
        await schedule_cmd.finish(f"已设置全局任务: {msg}")


@schedule_cmd.assign("删除")
async def _(
    event: GroupMessageEvent,
    schedule_id: Match[int] = AlconnaMatch("schedule_id"),
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    group_id: Match[str] = AlconnaMatch("group_id"),
    all_enabled: Query[bool] = Query("删除.all"),
):
    if schedule_id.available:
        success, message = await scheduler_manager.remove_schedule_by_id(
            schedule_id.result
        )
        await schedule_cmd.finish(message)

    elif plugin_name.available:
        p_name = plugin_name.result
        if p_name not in scheduler_manager.get_registered_plugins():
            await schedule_cmd.finish(f"未找到插件 '{p_name}'。")

        if all_enabled.available:
            removed_count = await scheduler_manager.remove_schedule_for_all(p_name)
            message = (
                f"已取消了 {removed_count} 个群组的插件 '{p_name}' 定时任务。"
                if removed_count > 0
                else f"没有找到插件 '{p_name}' 的定时任务。"
            )
            await schedule_cmd.finish(message)

        elif group_id.available:
            success, message = await scheduler_manager.remove_schedule(
                p_name, group_id.result
            )
            await schedule_cmd.finish(message)

        else:
            gid = str(event.group_id)
            success, message = await scheduler_manager.remove_schedule(p_name, gid)
            await schedule_cmd.finish(message)

    elif all_enabled.available:
        if group_id.available:
            gid = group_id.result
            success, message = await scheduler_manager.remove_schedules_by_group(gid)
            await schedule_cmd.finish(message)
        else:
            count, message = await scheduler_manager.remove_all_schedules()
            await schedule_cmd.finish(message)

    else:
        await schedule_cmd.finish(
            "删除任务失败：请提供任务ID，或通过 -p <插件> 或 -all 指定要删除的任务。"
        )


@schedule_cmd.assign("暂停")
async def _(
    event: GroupMessageEvent,
    schedule_id: Match[int] = AlconnaMatch("schedule_id"),
    all_enabled: Query[bool] = Query("暂停.all"),
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    group_id: Match[str] = AlconnaMatch("group_id"),
):
    if schedule_id.available:
        success, message = await scheduler_manager.pause_schedule(schedule_id.result)
        await schedule_cmd.finish(message)

    elif plugin_name.available:
        p_name = plugin_name.result
        if p_name not in scheduler_manager.get_registered_plugins():
            await schedule_cmd.finish(f"未找到插件 '{p_name}'。")

        if all_enabled.available:
            count, message = await scheduler_manager.pause_schedules_by_plugin(p_name)
            await schedule_cmd.finish(message)
        elif group_id.available:
            gid = group_id.result
            success, message = await scheduler_manager.pause_schedule_by_plugin_group(
                p_name, gid
            )
            await schedule_cmd.finish(message)
        else:
            gid = str(event.group_id)
            success, message = await scheduler_manager.pause_schedule_by_plugin_group(
                p_name, gid
            )
            await schedule_cmd.finish(message)

    elif all_enabled.available:
        if group_id.available:
            gid = group_id.result
            count, message = await scheduler_manager.pause_schedules_by_group(gid)
            await schedule_cmd.finish(message)
        else:
            count, message = await scheduler_manager.pause_all_schedules()
            await schedule_cmd.finish(message)

    else:
        await schedule_cmd.finish("请提供任务ID、使用 -p <插件> 或 -all 选项。")


@schedule_cmd.assign("恢复")
async def _(
    event: GroupMessageEvent,
    schedule_id: Match[int] = AlconnaMatch("schedule_id"),
    all_enabled: Query[bool] = Query("恢复.all"),
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    group_id: Match[str] = AlconnaMatch("group_id"),
):
    if schedule_id.available:
        success, message = await scheduler_manager.resume_schedule(schedule_id.result)
        await schedule_cmd.finish(message)

    elif plugin_name.available:
        p_name = plugin_name.result
        if p_name not in scheduler_manager.get_registered_plugins():
            await schedule_cmd.finish(f"未找到插件 '{p_name}'。")

        if all_enabled.available:
            count, message = await scheduler_manager.resume_schedules_by_plugin(p_name)
            await schedule_cmd.finish(message)
        elif group_id.available:
            gid = group_id.result
            success, message = await scheduler_manager.resume_schedule_by_plugin_group(
                p_name, gid
            )
            await schedule_cmd.finish(message)
        else:
            gid = str(event.group_id)
            success, message = await scheduler_manager.resume_schedule_by_plugin_group(
                p_name, gid
            )
            await schedule_cmd.finish(message)

    elif all_enabled.available:
        if group_id.available:
            gid = group_id.result
            count, message = await scheduler_manager.resume_schedules_by_group(gid)
            await schedule_cmd.finish(message)
        else:
            count, message = await scheduler_manager.resume_all_schedules()
            await schedule_cmd.finish(message)

    else:
        await schedule_cmd.finish("请提供任务ID、使用 -p <插件> 或 -all 选项。")


@schedule_cmd.assign("执行")
async def _(schedule_id: int):
    success, message = await scheduler_manager.trigger_now(schedule_id)
    await schedule_cmd.finish(message)


@schedule_cmd.assign("更新")
async def _(schedule_id: int, time: Match[str], kwargs_str: Match[str]):
    if not time.available and not kwargs_str.available:
        await schedule_cmd.finish("请提供需要更新的时间 (--time) 或参数 (--kwargs)。")

    trigger_config = None
    if time.available:
        try:
            time_parts = time.result.split(":")
            if len(time_parts) != 2:
                raise ValueError("时间格式应为 HH:MM")
            hour, minute = map(int, time_parts)
            if not (0 <= hour <= 23) or not (0 <= minute <= 59):
                raise ValueError("小时应在 0-23 范围内，分钟应在 0-59 范围内")
            trigger_config = {"hour": hour, "minute": minute}
        except ValueError as e:
            await schedule_cmd.finish(f"时间格式错误: {e}")

    job_kwargs = None
    if kwargs_str.available:
        schedule = await scheduler_manager.get_schedule_by_id(schedule_id)
        if not schedule:
            await schedule_cmd.finish(f"未找到 ID 为 {schedule_id} 的任务。")

        if schedule.plugin_name not in scheduler_manager._registered_tasks:
            await schedule_cmd.finish(f"插件 '{schedule.plugin_name}' 未注册定时任务。")

        task_meta = scheduler_manager._registered_tasks[schedule.plugin_name]
        if "params" not in task_meta or not task_meta["params"]:
            await schedule_cmd.finish(
                f"插件 '{schedule.plugin_name}' 未定义参数元数据。"
                f"请联系插件开发者更新插件注册代码。"
            )

        registered_params = task_meta["params"]
        job_kwargs = {}
        try:
            for item in kwargs_str.result.split(","):
                key, value = item.strip().split("=", 1)
                key = key.strip()

                if key not in registered_params:
                    await schedule_cmd.finish(
                        f"错误：插件不支持参数 '{key}'。"
                        f"可用参数: {list(registered_params.keys())}"
                    )

                param_meta = registered_params[key]
                if "type" not in param_meta:
                    await schedule_cmd.finish(
                        f"插件 '{schedule.plugin_name}' 的参数 '{key}' 未定义类型。"
                        f"请联系插件开发者更新参数元数据。"
                    )
                param_type = param_meta["type"]
                try:
                    job_kwargs[key] = param_type(value)
                except (ValueError, TypeError):
                    await schedule_cmd.finish(
                        f"参数 '{key}' 的值 '{value}' 格式不正确，"
                        f"应为 {param_type.__name__} 类型。"
                    )

        except Exception as e:
            await schedule_cmd.finish(
                f"参数格式错误，请使用 'key=value,key2=value2' 格式。错误: {e}"
            )

    success, message = await scheduler_manager.update_schedule(
        schedule_id, trigger_config, job_kwargs
    )
    await schedule_cmd.finish(message)


@schedule_cmd.assign("插件列表")
async def _():
    registered_plugins = scheduler_manager.get_registered_plugins()
    if not registered_plugins:
        await schedule_cmd.finish("当前没有已注册的定时任务插件。")

    message_parts = ["📋 已注册的定时任务插件:"]
    for i, plugin_name in enumerate(registered_plugins, 1):
        task_meta = scheduler_manager._registered_tasks[plugin_name]
        if "params" not in task_meta:
            message_parts.append(f"{i}. {plugin_name} - ⚠️ 未定义参数元数据")
            continue

        params = task_meta["params"]
        if params:
            param_info = ", ".join(
                f"{k}({v['type'].__name__})" for k, v in params.items()
            )
            message_parts.append(f"{i}. {plugin_name} - 参数: {param_info}")
        else:
            message_parts.append(f"{i}. {plugin_name} - 无参数")

    await schedule_cmd.finish("\n".join(message_parts))

from datetime import datetime

from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot
from nonebot.params import Depends
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna import AlconnaMatch, Arparma, Match, Query
from pydantic import BaseModel, ValidationError

from zhenxun.models.schedule_info import ScheduleInfo
from zhenxun.services.scheduler import scheduler_manager
from zhenxun.services.scheduler.targeter import ScheduleTargeter
from zhenxun.utils.message import MessageUtils

from . import presenters
from .commands import (
    GetBotId,
    GetTargeter,
    parse_daily_time,
    parse_interval,
    schedule_cmd,
)


@schedule_cmd.handle()
async def _handle_time_options_mutex(arp: Arparma):
    time_options = ["cron", "interval", "date", "daily"]
    provided_options = [opt for opt in time_options if arp.query(opt) is not None]
    if len(provided_options) > 1:
        await schedule_cmd.finish(
            f"时间选项 --{', --'.join(provided_options)} 不能同时使用，请只选择一个。"
        )


@schedule_cmd.assign("查看")
async def handle_view(
    bot: Bot,
    event: Event,
    target_group_id: Match[str] = AlconnaMatch("target_group_id"),
    all_groups: Query[bool] = Query("查看.all"),
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    page: Match[int] = AlconnaMatch("page"),
):
    is_superuser = await SUPERUSER(bot, event)
    title = ""
    gid_filter = None

    current_group_id = getattr(event, "group_id", None)
    if not (all_groups.available or target_group_id.available) and not current_group_id:
        await schedule_cmd.finish("私聊中查看任务必须使用 -g <群号> 或 -all 选项。")

    if all_groups.available:
        if not is_superuser:
            await schedule_cmd.finish("需要超级用户权限才能查看所有群组的定时任务。")
        title = "所有群组的定时任务"
    elif target_group_id.available:
        if not is_superuser:
            await schedule_cmd.finish("需要超级用户权限才能查看指定群组的定时任务。")
        gid_filter = target_group_id.result
        title = f"群 {gid_filter} 的定时任务"
    else:
        gid_filter = str(current_group_id)
        title = "本群的定时任务"

    p_name_filter = plugin_name.result if plugin_name.available else None

    schedules = await scheduler_manager.get_schedules(
        plugin_name=p_name_filter, group_id=gid_filter
    )

    if p_name_filter:
        title += f" [插件: {p_name_filter}]"

    if not schedules:
        await schedule_cmd.finish("没有找到任何相关的定时任务。")

    img = await presenters.format_schedule_list_as_image(
        schedules=schedules, title=title, current_page=page.result
    )
    await MessageUtils.build_message(img).send(reply_to=True)


@schedule_cmd.assign("设置")
async def handle_set(
    event: Event,
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    cron_expr: Match[str] = AlconnaMatch("cron_expr"),
    interval_expr: Match[str] = AlconnaMatch("interval_expr"),
    date_expr: Match[str] = AlconnaMatch("date_expr"),
    daily_expr: Match[str] = AlconnaMatch("daily_expr"),
    group_id: Match[str] = AlconnaMatch("group_id"),
    kwargs_str: Match[str] = AlconnaMatch("kwargs_str"),
    all_enabled: Query[bool] = Query("设置.all"),
    bot_id_to_operate: str = Depends(GetBotId),
):
    if not plugin_name.available:
        await schedule_cmd.finish("设置任务时必须提供插件名称。")

    has_time_option = any(
        [
            cron_expr.available,
            interval_expr.available,
            date_expr.available,
            daily_expr.available,
        ]
    )
    if not has_time_option:
        await schedule_cmd.finish(
            "必须提供一种时间选项: --cron, --interval, --date, 或 --daily。"
        )

    p_name = plugin_name.result
    if p_name not in scheduler_manager.get_registered_plugins():
        await schedule_cmd.finish(
            f"插件 '{p_name}' 没有注册可用的定时任务。\n"
            f"可用插件: {list(scheduler_manager.get_registered_plugins())}"
        )

    trigger_type, trigger_config = "", {}
    try:
        if cron_expr.available:
            trigger_type, trigger_config = (
                "cron",
                dict(
                    zip(
                        ["minute", "hour", "day", "month", "day_of_week"],
                        cron_expr.result.split(),
                    )
                ),
            )
        elif interval_expr.available:
            trigger_type, trigger_config = (
                "interval",
                parse_interval(interval_expr.result),
            )
        elif date_expr.available:
            trigger_type, trigger_config = (
                "date",
                {"run_date": datetime.fromisoformat(date_expr.result)},
            )
        elif daily_expr.available:
            trigger_type, trigger_config = "cron", parse_daily_time(daily_expr.result)
        else:
            await schedule_cmd.finish(
                "必须提供一种时间选项: --cron, --interval, --date, 或 --daily。"
            )
    except ValueError as e:
        await schedule_cmd.finish(f"时间参数解析错误: {e}")

    job_kwargs = {}
    if kwargs_str.available:
        task_meta = scheduler_manager._registered_tasks[p_name]
        params_model = task_meta.get("model")
        if not (
            params_model
            and isinstance(params_model, type)
            and issubclass(params_model, BaseModel)
        ):
            await schedule_cmd.finish(f"插件 '{p_name}' 不支持或配置了无效的参数模型。")
        try:
            raw_kwargs = dict(
                item.strip().split("=", 1) for item in kwargs_str.result.split(",")
            )

            model_validate = getattr(params_model, "model_validate", None)
            if not model_validate:
                await schedule_cmd.finish(f"插件 '{p_name}' 的参数模型不支持验证")

            validated_model = model_validate(raw_kwargs)

            model_dump = getattr(validated_model, "model_dump", None)
            if not model_dump:
                await schedule_cmd.finish(f"插件 '{p_name}' 的参数模型不支持导出")

            job_kwargs = model_dump()
        except ValidationError as e:
            errors = [f"  - {err['loc'][0]}: {err['msg']}" for err in e.errors()]
            await schedule_cmd.finish(
                f"插件 '{p_name}' 的任务参数验证失败:\n" + "\n".join(errors)
            )
        except Exception as e:
            await schedule_cmd.finish(
                f"参数格式错误，请使用 'key=value,key2=value2' 格式。错误: {e}"
            )

    gid_str = group_id.result if group_id.available else None
    target_group_id = (
        scheduler_manager.ALL_GROUPS
        if (gid_str and gid_str.lower() == "all") or all_enabled.available
        else gid_str or getattr(event, "group_id", None)
    )
    if not target_group_id:
        await schedule_cmd.finish(
            "私聊中设置定时任务时，必须使用 -g <群号> 或 --all 选项指定目标。"
        )

    schedule = await scheduler_manager.add_schedule(
        p_name,
        str(target_group_id),
        trigger_type,
        trigger_config,
        job_kwargs,
        bot_id=bot_id_to_operate,
    )

    target_desc = (
        f"所有群组 (Bot: {bot_id_to_operate})"
        if target_group_id == scheduler_manager.ALL_GROUPS
        else f"群组 {target_group_id}"
    )

    if schedule:
        await schedule_cmd.finish(
            f"为 [{target_desc}] 已成功设置插件 '{p_name}' 的定时任务 "
            f"(ID: {schedule.id})。"
        )
    else:
        await schedule_cmd.finish(f"为 [{target_desc}] 设置任务失败。")


@schedule_cmd.assign("删除")
async def handle_delete(targeter: ScheduleTargeter = GetTargeter("删除")):
    schedules_to_remove: list[ScheduleInfo] = await targeter._get_schedules()
    if not schedules_to_remove:
        await schedule_cmd.finish("没有找到可删除的任务。")

    count, _ = await targeter.remove()

    if count > 0 and schedules_to_remove:
        if len(schedules_to_remove) == 1:
            message = presenters.format_remove_success(schedules_to_remove[0])
        else:
            target_desc = targeter._generate_target_description()
            message = f"✅ 成功移除了{target_desc} {count} 个任务。"
    else:
        message = "没有任务被移除。"
    await schedule_cmd.finish(message)


@schedule_cmd.assign("暂停")
async def handle_pause(targeter: ScheduleTargeter = GetTargeter("暂停")):
    schedules_to_pause: list[ScheduleInfo] = await targeter._get_schedules()
    if not schedules_to_pause:
        await schedule_cmd.finish("没有找到可暂停的任务。")

    count, _ = await targeter.pause()

    if count > 0 and schedules_to_pause:
        if len(schedules_to_pause) == 1:
            message = presenters.format_pause_success(schedules_to_pause[0])
        else:
            target_desc = targeter._generate_target_description()
            message = f"✅ 成功暂停了{target_desc} {count} 个任务。"
    else:
        message = "没有任务被暂停。"
    await schedule_cmd.finish(message)


@schedule_cmd.assign("恢复")
async def handle_resume(targeter: ScheduleTargeter = GetTargeter("恢复")):
    schedules_to_resume: list[ScheduleInfo] = await targeter._get_schedules()
    if not schedules_to_resume:
        await schedule_cmd.finish("没有找到可恢复的任务。")

    count, _ = await targeter.resume()

    if count > 0 and schedules_to_resume:
        if len(schedules_to_resume) == 1:
            message = presenters.format_resume_success(schedules_to_resume[0])
        else:
            target_desc = targeter._generate_target_description()
            message = f"✅ 成功恢复了{target_desc} {count} 个任务。"
    else:
        message = "没有任务被恢复。"
    await schedule_cmd.finish(message)


@schedule_cmd.assign("执行")
async def handle_trigger(schedule_id: Match[int] = AlconnaMatch("schedule_id")):
    from zhenxun.services.scheduler.repository import ScheduleRepository

    schedule_info = await ScheduleRepository.get_by_id(schedule_id.result)
    if not schedule_info:
        await schedule_cmd.finish(f"未找到 ID 为 {schedule_id.result} 的任务。")

    success, message = await scheduler_manager.trigger_now(schedule_id.result)

    if success:
        final_message = presenters.format_trigger_success(schedule_info)
    else:
        final_message = f"❌ 手动触发失败: {message}"
    await schedule_cmd.finish(final_message)


@schedule_cmd.assign("更新")
async def handle_update(
    schedule_id: Match[int] = AlconnaMatch("schedule_id"),
    cron_expr: Match[str] = AlconnaMatch("cron_expr"),
    interval_expr: Match[str] = AlconnaMatch("interval_expr"),
    date_expr: Match[str] = AlconnaMatch("date_expr"),
    daily_expr: Match[str] = AlconnaMatch("daily_expr"),
    kwargs_str: Match[str] = AlconnaMatch("kwargs_str"),
):
    if not any(
        [
            cron_expr.available,
            interval_expr.available,
            date_expr.available,
            daily_expr.available,
            kwargs_str.available,
        ]
    ):
        await schedule_cmd.finish(
            "请提供需要更新的时间 (--cron/--interval/--date/--daily) 或参数 (--kwargs)"
        )

    trigger_type, trigger_config, job_kwargs = None, None, None
    try:
        if cron_expr.available:
            trigger_type, trigger_config = (
                "cron",
                dict(
                    zip(
                        ["minute", "hour", "day", "month", "day_of_week"],
                        cron_expr.result.split(),
                    )
                ),
            )
        elif interval_expr.available:
            trigger_type, trigger_config = (
                "interval",
                parse_interval(interval_expr.result),
            )
        elif date_expr.available:
            trigger_type, trigger_config = (
                "date",
                {"run_date": datetime.fromisoformat(date_expr.result)},
            )
        elif daily_expr.available:
            trigger_type, trigger_config = "cron", parse_daily_time(daily_expr.result)
    except ValueError as e:
        await schedule_cmd.finish(f"时间参数解析错误: {e}")

    if kwargs_str.available:
        job_kwargs = dict(
            item.strip().split("=", 1) for item in kwargs_str.result.split(",")
        )

    success, message = await scheduler_manager.update_schedule(
        schedule_id.result, trigger_type, trigger_config, job_kwargs
    )

    if success:
        from zhenxun.services.scheduler.repository import ScheduleRepository

        updated_schedule = await ScheduleRepository.get_by_id(schedule_id.result)
        if updated_schedule:
            final_message = presenters.format_update_success(updated_schedule)
        else:
            final_message = "✅ 更新成功，但无法获取更新后的任务详情。"
    else:
        final_message = f"❌ 更新失败: {message}"

    await schedule_cmd.finish(final_message)


@schedule_cmd.assign("插件列表")
async def handle_plugins_list():
    message = await presenters.format_plugins_list()
    await schedule_cmd.finish(message)


@schedule_cmd.assign("状态")
async def handle_status(schedule_id: Match[int] = AlconnaMatch("schedule_id")):
    status = await scheduler_manager.get_schedule_status(schedule_id.result)
    if not status:
        await schedule_cmd.finish(f"未找到ID为 {schedule_id.result} 的定时任务。")

    message = presenters.format_single_status_message(status)
    await schedule_cmd.finish(message)

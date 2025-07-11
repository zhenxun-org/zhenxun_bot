import asyncio

from zhenxun.models.schedule_info import ScheduleInfo
from zhenxun.services.scheduler import scheduler_manager
from zhenxun.utils._image_template import ImageTemplate, RowStyle


def _get_type_name(annotation) -> str:
    """获取类型注解的名称"""
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    elif hasattr(annotation, "_name"):
        return annotation._name
    else:
        return str(annotation)


def _format_trigger(schedule: dict) -> str:
    """格式化触发器信息为可读字符串"""
    trigger_type = schedule.get("trigger_type")
    config = schedule.get("trigger_config")

    if not isinstance(config, dict):
        return f"配置错误: {config}"

    if trigger_type == "cron":
        hour = config.get("hour", "??")
        minute = config.get("minute", "??")
        try:
            hour_int = int(hour)
            minute_int = int(minute)
            return f"每天 {hour_int:02d}:{minute_int:02d}"
        except (ValueError, TypeError):
            return f"每天 {hour}:{minute}"
    elif trigger_type == "interval":
        units = {
            "weeks": "周",
            "days": "天",
            "hours": "小时",
            "minutes": "分钟",
            "seconds": "秒",
        }
        for unit, unit_name in units.items():
            if value := config.get(unit):
                return f"每 {value} {unit_name}"
        return "未知间隔"
    elif trigger_type == "date":
        run_date = config.get("run_date", "N/A")
        return f"特定时间 {run_date}"
    else:
        return f"未知触发器类型: {trigger_type}"


def _format_trigger_for_card(schedule_info: ScheduleInfo | dict) -> str:
    """为信息卡片格式化触发器规则"""
    trigger_type = (
        schedule_info.get("trigger_type")
        if isinstance(schedule_info, dict)
        else schedule_info.trigger_type
    )
    config = (
        schedule_info.get("trigger_config")
        if isinstance(schedule_info, dict)
        else schedule_info.trigger_config
    )

    if not isinstance(config, dict):
        return f"配置错误: {config}"

    if trigger_type == "cron":
        hour = config.get("hour", "??")
        minute = config.get("minute", "??")
        try:
            hour_int = int(hour)
            minute_int = int(minute)
            return f"每天 {hour_int:02d}:{minute_int:02d}"
        except (ValueError, TypeError):
            return f"每天 {hour}:{minute}"
    elif trigger_type == "interval":
        units = {
            "weeks": "周",
            "days": "天",
            "hours": "小时",
            "minutes": "分钟",
            "seconds": "秒",
        }
        for unit, unit_name in units.items():
            if value := config.get(unit):
                return f"每 {value} {unit_name}"
        return "未知间隔"
    elif trigger_type == "date":
        run_date = config.get("run_date", "N/A")
        return f"特定时间 {run_date}"
    else:
        return f"未知规则: {trigger_type}"


def _format_operation_result_card(
    title: str, schedule_info: ScheduleInfo, extra_info: list[str] | None = None
) -> str:
    """
    生成一个标准的操作结果信息卡片。

    参数:
        title: 卡片的标题 (例如 "✅ 成功暂停定时任务!")
        schedule_info: 相关的 ScheduleInfo 对象
        extra_info: (可选) 额外的补充信息行
    """
    target_desc = (
        f"群组 {schedule_info.group_id}"
        if schedule_info.group_id
        and schedule_info.group_id != scheduler_manager.ALL_GROUPS
        else "所有群组"
        if schedule_info.group_id == scheduler_manager.ALL_GROUPS
        else "全局"
    )

    info_lines = [
        title,
        f"✓ 任务 ID: {schedule_info.id}",
        f"🖋 插件: {schedule_info.plugin_name}",
        f"🎯 目标: {target_desc}",
        f"⏰ 时间: {_format_trigger_for_card(schedule_info)}",
    ]
    if extra_info:
        info_lines.extend(extra_info)

    return "\n".join(info_lines)


def format_pause_success(schedule_info: ScheduleInfo) -> str:
    """格式化暂停成功的消息"""
    return _format_operation_result_card("✅ 成功暂停定时任务!", schedule_info)


def format_resume_success(schedule_info: ScheduleInfo) -> str:
    """格式化恢复成功的消息"""
    return _format_operation_result_card("▶️ 成功恢复定时任务!", schedule_info)


def format_remove_success(schedule_info: ScheduleInfo) -> str:
    """格式化删除成功的消息"""
    return _format_operation_result_card("❌ 成功删除定时任务!", schedule_info)


def format_trigger_success(schedule_info: ScheduleInfo) -> str:
    """格式化手动触发成功的消息"""
    return _format_operation_result_card("🚀 成功手动触发定时任务!", schedule_info)


def format_update_success(schedule_info: ScheduleInfo) -> str:
    """格式化更新成功的消息"""
    return _format_operation_result_card("🔄️ 成功更新定时任务配置!", schedule_info)


def _status_row_style(column: str, text: str) -> RowStyle:
    """为状态列设置颜色"""
    style = RowStyle()
    if column == "状态":
        if text == "启用":
            style.font_color = "#67C23A"
        elif text == "暂停":
            style.font_color = "#F56C6C"
        elif text == "运行中":
            style.font_color = "#409EFF"
    return style


def _format_params(schedule_status: dict) -> str:
    """将任务参数格式化为人类可读的字符串"""
    if kwargs := schedule_status.get("job_kwargs"):
        return " | ".join(f"{k}: {v}" for k, v in kwargs.items())
    return "-"


async def format_schedule_list_as_image(
    schedules: list[ScheduleInfo], title: str, current_page: int
):
    """将任务列表格式化为图片"""
    page_size = 15
    total_items = len(schedules)
    total_pages = (total_items + page_size - 1) // page_size
    start_index = (current_page - 1) * page_size
    end_index = start_index + page_size
    paginated_schedules = schedules[start_index:end_index]

    if not paginated_schedules:
        return "这一页没有内容了哦~"

    status_tasks = [
        scheduler_manager.get_schedule_status(s.id) for s in paginated_schedules
    ]
    all_statuses = await asyncio.gather(*status_tasks)

    def get_status_text(status_value):
        if isinstance(status_value, bool):
            return "启用" if status_value else "暂停"
        return str(status_value)

    data_list = [
        [
            s["id"],
            s["plugin_name"],
            s.get("bot_id") or "N/A",
            s["group_id"] or "全局",
            s["next_run_time"],
            _format_trigger(s),
            _format_params(s),
            get_status_text(s["is_enabled"]),
        ]
        for s in all_statuses
        if s
    ]

    if not data_list:
        return "没有找到任何相关的定时任务。"

    return await ImageTemplate.table_page(
        head_text=title,
        tip_text=f"第 {current_page}/{total_pages} 页，共 {total_items} 条任务",
        column_name=["ID", "插件", "Bot", "目标", "下次运行", "规则", "参数", "状态"],
        data_list=data_list,
        column_space=20,
        text_style=_status_row_style,
    )


def format_single_status_message(status: dict) -> str:
    """格式化单个任务状态为文本消息"""
    info_lines = [
        f"📋 定时任务详细信息 (ID: {status['id']})",
        "--------------------",
        f"▫️ 插件: {status['plugin_name']}",
        f"▫️ Bot ID: {status.get('bot_id') or '默认'}",
        f"▫️ 目标: {status['group_id'] or '全局'}",
        f"▫️ 状态: {'✔️ 已启用' if status['is_enabled'] else '⏸️ 已暂停'}",
        f"▫️ 下次运行: {status['next_run_time']}",
        f"▫️ 触发规则: {_format_trigger(status)}",
        f"▫️ 任务参数: {_format_params(status)}",
    ]
    return "\n".join(info_lines)


async def format_plugins_list() -> str:
    """格式化可用插件列表为文本消息"""
    from pydantic import BaseModel

    registered_plugins = scheduler_manager.get_registered_plugins()
    if not registered_plugins:
        return "当前没有已注册的定时任务插件。"

    message_parts = ["📋 已注册的定时任务插件:"]
    for i, plugin_name in enumerate(registered_plugins, 1):
        task_meta = scheduler_manager._registered_tasks[plugin_name]
        params_model = task_meta.get("model")

        param_info_str = "无参数"
        if (
            params_model
            and isinstance(params_model, type)
            and issubclass(params_model, BaseModel)
        ):
            model_fields = getattr(params_model, "model_fields", None)
            if model_fields:
                param_info_str = "参数: " + ", ".join(
                    f"{field_name}({_get_type_name(field_info.annotation)})"
                    for field_name, field_info in model_fields.items()
                )
        elif params_model:
            param_info_str = "⚠️ 参数模型配置错误"

        message_parts.append(f"{i}. {plugin_name} - {param_info_str}")

    return "\n".join(message_parts)

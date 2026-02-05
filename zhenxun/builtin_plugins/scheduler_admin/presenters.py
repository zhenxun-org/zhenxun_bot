from typing import Any

from zhenxun import ui
from zhenxun.models.scheduled_job import ScheduledJob
from zhenxun.services import scheduler_manager
from zhenxun.ui.models import StatusBadgeCell, TextCell
from zhenxun.utils.pydantic_compat import model_json_schema


def _get_schedule_attr(schedule: ScheduledJob | dict, attr_name: str) -> Any:
    """兼容地从字典或对象获取属性"""
    if isinstance(schedule, dict):
        return schedule.get(attr_name)
    return getattr(schedule, attr_name, None)


def _format_trigger_info(schedule: ScheduledJob | dict) -> str:
    """格式化触发器信息为可读字符串（兼容字典和对象）"""
    trigger_type = _get_schedule_attr(schedule, "trigger_type")
    config = _get_schedule_attr(schedule, "trigger_config")

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


def _format_operation_result_card(
    title: str, schedule_info: ScheduledJob, extra_info: list[str] | None = None
) -> str:
    """
    生成一个标准的操作结果信息卡片。

    参数:
        title: 卡片的标题 (例如 "✅ 成功暂停定时任务!")
        schedule_info: 相关的 ScheduledJob 对象
        extra_info: (可选) 额外的补充信息行
    """
    target_desc = format_target_info(
        schedule_info.target_type, schedule_info.target_identifier
    )

    info_lines = [
        title,
        f"✓ 任务 ID: {schedule_info.id}",
        f"🖋 插件: {schedule_info.plugin_name}",
        f"🎯 目标: {target_desc}",
        f"⏰ 时间: {_format_trigger_info(schedule_info)}",
    ]
    if extra_info:
        info_lines.extend(extra_info)

    return "\n".join(info_lines)


def format_pause_success(schedule_info: ScheduledJob) -> str:
    """格式化暂停成功的消息"""
    return _format_operation_result_card("✅ 成功暂停定时任务!", schedule_info)


def format_resume_success(schedule_info: ScheduledJob) -> str:
    """格式化恢复成功的消息"""
    return _format_operation_result_card("▶️ 成功恢复定时任务!", schedule_info)


def format_remove_success(schedule_info: ScheduledJob) -> str:
    """格式化删除成功的消息"""
    return _format_operation_result_card("❌ 成功删除定时任务!", schedule_info)


def format_trigger_success(schedule_info: ScheduledJob) -> str:
    """格式化手动触发成功的消息"""
    return _format_operation_result_card("🚀 成功手动触发定时任务!", schedule_info)


def format_update_success(schedule_info: ScheduledJob) -> str:
    """格式化更新成功的消息"""
    return _format_operation_result_card("🔄️ 成功更新定时任务配置!", schedule_info)


def _format_params(schedule_status: dict) -> str:
    """将任务参数格式化为人类可读的字符串"""
    if kwargs := schedule_status.get("job_kwargs"):
        return " | ".join(f"{k}: {v}" for k, v in kwargs.items())
    return "-"


async def format_schedule_list_as_image(
    schedules: list[ScheduledJob], title: str, current_page: int, total_items: int
):
    """将任务列表格式化为图片"""
    page_size = 30
    total_pages = (total_items + page_size - 1) // page_size

    if not schedules:
        return "这一页没有内容了哦~"

    schedule_ids = [s.id for s in schedules]
    all_statuses_list = await scheduler_manager.get_schedules_status_bulk(schedule_ids)
    all_statuses_map = {status["id"]: status for status in all_statuses_list}

    data_list = []
    for schedule_db in schedules:
        s = all_statuses_map.get(schedule_db.id)
        if not s:
            continue

        status_value = s["is_enabled"]
        if status_value == "运行中":
            status_cell = StatusBadgeCell(text="运行中", status_type="info")
        else:
            is_enabled = status_value == "启用"
            status_cell = StatusBadgeCell(
                text="启用" if is_enabled else "暂停",
                status_type="ok" if is_enabled else "error",
            )

        data_list.append(
            [
                TextCell(content=str(s["id"])),
                TextCell(content=s["plugin_name"]),
                TextCell(content=s.get("bot_id") or "N/A"),
                TextCell(
                    content=format_target_info(s["target_type"], s["target_identifier"])
                ),
                TextCell(content=s["next_run_time"]),
                TextCell(content=_format_trigger_info(s)),
                TextCell(content=_format_params(s)),
                status_cell,
            ]
        )

    if not data_list:
        return "没有找到任何相关的定时任务。"

    table = ui.table(
        title, f"第 {current_page}/{total_pages} 页，共 {total_items} 条任务"
    )
    table.set_headers(
        ["ID", "插件", "Bot", "目标", "下次运行", "规则", "参数", "状态"]
    ).add_rows(data_list)
    return await ui.render(
        table,
        viewport={"width": 1400, "height": 10},
        device_scale_factor=2,
    )


def format_target_info(target_type: str, target_identifier: str) -> str:
    """格式化目标信息以供显示"""
    if target_type == "GLOBAL":
        return "全局"
    elif target_type == "ALL_GROUPS":
        return "所有群组"
    elif target_type == "TAG":
        return f"标签: {target_identifier}"
    elif target_type == "GROUP":
        return f"群: {target_identifier}"
    elif target_type == "USER":
        return f"用户: {target_identifier}"
    else:
        return f"{target_type}: {target_identifier}"


def format_single_status_message(status: dict) -> str:
    """格式化单个任务状态为文本消息"""
    target_info = format_target_info(status["target_type"], status["target_identifier"])
    trigger_info = status.get("trigger_info_str", _format_trigger_info(status))
    info_lines = [
        f"📋 定时任务详细信息 (ID: {status['id']})",
        "--------------------",
        f"▫️ 插件: {status['plugin_name']}",
        f"▫️ Bot ID: {status.get('bot_id') or '默认'}",
        f"▫️ 目标: {target_info}",
        f"▫️ 状态: {'✔️ 已启用' if status['is_enabled'] else '⏸️ 已暂停'}",
        f"▫️ 下次运行: {status['next_run_time']}",
        f"▫️ 触发规则: {trigger_info}",
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
            schema = model_json_schema(params_model)
            properties = schema.get("properties", {})
            if properties:
                param_info_str = "参数: " + ", ".join(
                    f"{field_name}({prop.get('type', 'any')})"
                    for field_name, prop in properties.items()
                )
        elif params_model:
            param_info_str = "⚠️ 参数模型配置错误"

        message_parts.append(f"{i}. {plugin_name} - {param_info_str}")

    return "\n".join(message_parts)

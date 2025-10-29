from datetime import datetime
import re
from typing import Any

from arclet.alconna import Alconna
from nonebot.adapters import Bot, Event
from nonebot.params import Depends
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna import (
    AlconnaMatch,
    AlconnaMatcher,
    AlconnaMatches,
    AlconnaQuery,
    Arparma,
    Match,
    Query,
)
from nonebot_plugin_session import EventSession
from nonebot_plugin_uninfo import Uninfo

from zhenxun.configs.config import Config
from zhenxun.models.level_user import LevelUser
from zhenxun.models.scheduled_job import ScheduledJob
from zhenxun.services import scheduler_manager
from zhenxun.utils.time_utils import TimeUtils


async def GetCreatorPermissionLevel(
    bot: Bot,
    event: Event,
    session: Uninfo,
) -> int:
    """
    依赖注入函数：获取执行命令的用户的权限等级。
    """
    is_superuser = await SUPERUSER(bot, event)
    if is_superuser:
        return 999

    current_group_id = session.group.id if session.group else None
    return await LevelUser.get_user_level(session.user.id, current_group_id)


async def RequireTaskPermission(
    matcher: AlconnaMatcher,
    bot: Bot,
    event: Event,
    session: EventSession,
    schedule_id_match: Match[int] = AlconnaMatch("schedule_id"),
) -> ScheduledJob:
    """
    依赖注入函数：获取并验证用户对特定任务的操作权限。
    """
    if not schedule_id_match.available:
        await matcher.finish("此操作需要一个有效的任务ID。")

    schedule_id = schedule_id_match.result
    schedule = await scheduler_manager.get_schedule_by_id(schedule_id)
    if not schedule:
        await matcher.finish(f"未找到ID为 {schedule_id} 的任务。")

    is_superuser = await SUPERUSER(bot, event)
    if is_superuser:
        return schedule

    user_id = session.id1
    if not user_id:
        await matcher.finish("无法获取用户信息，权限检查失败。")

    group_id = session.id3 or session.id2
    user_level = await LevelUser.get_user_level(user_id, group_id)

    if user_level < schedule.required_permission:
        await matcher.finish(
            f"权限不足！操作此任务需要 {schedule.required_permission} 级权限，"
            f"您当前为 {user_level} 级。"
        )

    return schedule


def parse_daily_time(time_str: str) -> dict:
    """解析每日时间字符串为 cron 配置字典"""
    if match := re.match(r"^(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?$", time_str):
        hour, minute, second = match.groups()
        hour, minute = int(hour), int(minute)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("小时或分钟数值超出范围。")
        cron_config = {
            "minute": str(minute),
            "hour": str(hour),
            "day": "*",
            "month": "*",
            "day_of_week": "*",
            "timezone": Config.get_config("SchedulerManager", "SCHEDULER_TIMEZONE"),
        }
        if second is not None:
            if not (0 <= int(second) <= 59):
                raise ValueError("秒数值超出范围。")
            cron_config["second"] = str(second)
        return cron_config
    else:
        raise ValueError("时间格式错误，请使用 'HH:MM' 或 'HH:MM:SS' 格式。")


def _parse_trigger_from_arparma(arp: Arparma) -> tuple[str, dict] | None:
    """从 Arparma 中解析时间触发器配置"""
    subcommand_name = next(iter(arp.subcommands.keys()), None)
    if not subcommand_name:
        return None

    try:
        if cron_expr := arp.query[str](f"{subcommand_name}.cron.cron_expr", None):
            return "cron", dict(
                zip(
                    ["minute", "hour", "day", "month", "day_of_week"], cron_expr.split()
                )
            )
        if interval_expr := arp.query[str](
            f"{subcommand_name}.interval.interval_expr", None
        ):
            return "interval", TimeUtils.parse_interval_to_dict(interval_expr)
        if date_expr := arp.query[str](f"{subcommand_name}.date.date_expr", None):
            return "date", {"run_date": datetime.fromisoformat(date_expr)}
        if daily_expr := arp.query[str](f"{subcommand_name}.daily.daily_expr", None):
            return "cron", parse_daily_time(daily_expr)
    except ValueError as e:
        raise ValueError(f"时间参数解析错误: {e}") from e
    return None


async def GetTriggerInfo(
    matcher: AlconnaMatcher,
    arp: Arparma = AlconnaMatches(),
) -> tuple[str, dict]:
    """依赖注入函数：解析并验证时间触发器"""
    try:
        trigger_info = _parse_trigger_from_arparma(arp)
        if trigger_info:
            return trigger_info
    except ValueError as e:
        await matcher.finish(f"时间参数解析错误: {e}")

    await matcher.finish(
        "必须提供一种时间选项: --cron, --interval, --date, 或 --daily。"
    )


async def GetBotId(bot: Bot, bot_id_match: Match[str] = AlconnaMatch("bot_id")) -> str:
    """依赖注入函数：获取要操作的Bot ID"""
    if bot_id_match.available:
        return bot_id_match.result
    return bot.self_id


async def GetTargeter(
    matcher: AlconnaMatcher,
    event: Event,
    bot: Bot,
    arp: Arparma = AlconnaMatches(),
    schedule_ids: Match[list[int]] = AlconnaMatch("schedule_ids"),
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    group_ids: Match[list[str]] = AlconnaMatch("group_ids"),
    user_id: Match[str] = AlconnaMatch("user_id"),
    tag_name: Match[str] = AlconnaMatch("tag_name"),
    bot_id_to_operate: str = Depends(GetBotId),
) -> Any:
    """
    依赖注入函数，用于解析命令参数并返回一个配置好的 ScheduleTargeter 实例。
    """
    subcommand = next(iter(arp.subcommands.keys()), None)
    if not subcommand:
        await matcher.finish("内部错误：无法解析子命令。")

    if schedule_ids.available:
        return scheduler_manager.target(id__in=schedule_ids.result)

    all_enabled = arp.query(f"{subcommand}.all.value", False)
    global_flag = arp.query(f"{subcommand}.global.value", False)

    if not any(
        [
            plugin_name.available,
            all_enabled,
            global_flag,
            user_id.available,
            group_ids.available,
            tag_name.available,
            getattr(event, "group_id", None),
        ]
    ):
        await matcher.finish(
            f"'{subcommand}'操作失败：请提供任务ID，"
            f"或通过 -p <插件名> / --global / --all 指定要操作的任务。"
        )

    filters: dict[str, Any] = {"bot_id": bot_id_to_operate}
    if plugin_name.available:
        filters["plugin_name"] = plugin_name.result

    if global_flag:
        filters["target_type"] = "ALL_GROUPS"
        filters["target_identifier"] = scheduler_manager.ALL_GROUPS
    elif user_id.available:
        filters["target_type"] = "USER"
        filters["target_identifier"] = user_id.result
    elif all_enabled:
        pass
    elif tag_name.available:
        filters["target_type"] = "TAG"
        filters["target_identifier"] = tag_name.result
    elif group_ids.available:
        gids = [str(gid) for gid in group_ids.result]
        filters["target_type"] = "GROUP"
        filters["target_identifier__in"] = gids
    else:
        current_group_id = getattr(event, "group_id", None)
        if current_group_id:
            filters["target_type"] = "GROUP"
            filters["target_identifier"] = str(current_group_id)

    return scheduler_manager.target(**filters)


async def GetValidatedJobKwargs(
    matcher: AlconnaMatcher,
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    cli_string: Match[str] = AlconnaMatch("cli_string"),
    kwargs_str: Match[str] = AlconnaMatch("kwargs_str"),
) -> dict:
    """依赖注入函数：解析、合并和验证任务的关键字参数"""
    p_name = plugin_name.result
    task_meta = scheduler_manager._registered_tasks.get(p_name)
    if not task_meta:
        await matcher.finish(f"插件 '{p_name}' 未注册可定时执行的任务。")

    cli_kwargs = {}
    if cli_string.available and cli_string.result.strip():
        if not (cli_parser := task_meta.get("cli_parser")):
            await matcher.finish(
                f"插件 '{p_name}' 不支持通过 --params-cli 设置参数，"
                f"因为它没有注册解析器。"
            )

        try:
            temp_parser = Alconna("_", cli_parser.args, *cli_parser.options)  # type: ignore
            parsed_cli = temp_parser.parse(f"_ {cli_string.result.strip()}")

            if not parsed_cli.matched:
                raise ValueError(f"参数无法匹配: {parsed_cli.error_info or '未知错误'}")

            cli_kwargs = parsed_cli.all_matched_args

        except Exception as e:
            await matcher.finish(
                f"使用 --params-cli 解析参数失败: {e}\n\n请确保参数格式与插件命令一致。"
            )

    explicit_kwargs = {}
    if kwargs_str.available and kwargs_str.result.strip():
        try:
            explicit_kwargs = dict(
                item.strip().split("=", 1)
                for item in kwargs_str.result.split(";")
                if item.strip()
            )
        except ValueError:
            await matcher.finish(
                "参数格式错误，--kwargs 请使用 'key=value;key2=value2' 格式。"
            )

    final_job_kwargs = {**cli_kwargs, **explicit_kwargs}

    is_valid, result = scheduler_manager._validate_and_prepare_kwargs(
        p_name, final_job_kwargs
    )
    if not is_valid:
        await matcher.finish(f"任务参数校验失败:\n{result}")

    return result if isinstance(result, dict) else {}


async def GetFinalPermission(
    matcher: AlconnaMatcher,
    bot: Bot,
    event: Event,
    session: Uninfo,
    plugin_name: Match[str] = AlconnaMatch("plugin_name"),
    perm_level: Match[int] = AlconnaMatch("perm_level"),
) -> int:
    """依赖注入函数：计算任务的最终权限等级"""
    is_superuser = await SUPERUSER(bot, event)
    current_group_id = session.group.id if session.group else None

    if is_superuser:
        effective_user_level = 9
    else:
        effective_user_level = await LevelUser.get_user_level(
            session.user.id, current_group_id
        )
    if perm_level.available:
        requested_perm_level = perm_level.result
        if not is_superuser and requested_perm_level > effective_user_level:
            await matcher.send(
                f"⚠️ 警告：您指定的权限等级 ({requested_perm_level}) "
                f"高于自身权限 ({effective_user_level})。\n"
                f"任务的管理权限已被自动设置为 {effective_user_level} 级。"
            )
            return effective_user_level
        return requested_perm_level

    else:
        base_permission = effective_user_level
        task_meta = scheduler_manager._registered_tasks.get(plugin_name.result)
        if task_meta and "default_permission" in task_meta:
            default_perm = task_meta.get("default_permission")
            if isinstance(default_perm, int):
                base_permission = default_perm

        return min(base_permission, effective_user_level)


async def ResolveTargets(
    matcher: AlconnaMatcher,
    bot: Bot,
    event: Event,
    session: Uninfo,
    group_ids: Match[list[str]] = AlconnaMatch("group_ids"),
    tag_name: Match[str] = AlconnaMatch("tag_name"),
    user_id: Match[str] = AlconnaMatch("user_id"),
    all_flag: Query[bool] = AlconnaQuery("设置.all.value", False),
    global_flag: Query[bool] = AlconnaQuery("设置.global.value", False),
) -> list[str]:
    """依赖注入函数，用于解析和计算最终的目标描述符列表，并进行权限检查"""
    is_superuser = await SUPERUSER(bot, event)
    current_group_id = session.group.id if session.group else None

    if not is_superuser:
        permission_denied = False
        if (
            global_flag.result
            or all_flag.result
            or tag_name.available
            or user_id.available
        ):
            permission_denied = True
        elif group_ids.available and any(
            str(gid) != str(current_group_id) for gid in group_ids.result
        ):
            permission_denied = True

        if permission_denied:
            await matcher.finish(
                "权限不足，只有超级用户才能为其他群组、所有群组或通过标签设置任务。"
            )

    if user_id.available:
        return [user_id.result]
    if all_flag.result or global_flag.result:
        return [scheduler_manager.ALL_GROUPS]
    if tag_name.available:
        return [f"tag:{tag_name.result}"]
    if group_ids.available:
        return group_ids.result
    if current_group_id:
        return [str(current_group_id)]

    await matcher.finish(
        "私聊中设置任务必须使用 -u, -g, --all, --global 或 -t 选项指定目标。"
    )

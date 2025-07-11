import re

from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot
from nonebot.params import Depends
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

from zhenxun.configs.config import Config
from zhenxun.services.scheduler import scheduler_manager
from zhenxun.services.scheduler.targeter import ScheduleTargeter
from zhenxun.utils.rules import admin_check

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
            Args["plugin_name", str],
            Option("--cron", Args["cron_expr", str], help_text="设置 cron 表达式"),
            Option("--interval", Args["interval_expr", str], help_text="设置时间间隔"),
            Option("--date", Args["date_expr", str], help_text="设置特定执行日期"),
            Option(
                "--daily",
                Args["daily_expr", str],
                help_text="设置每天执行的时间 (如 08:20)",
            ),
            Option("-g", Args["group_id", str], help_text="指定群组ID或'all'"),
            Option("-all", help_text="对所有群生效 (等同于 -g all)"),
            Option("--kwargs", Args["kwargs_str", str], help_text="设置任务参数"),
            Option(
                "--bot", Args["bot_id", str], help_text="指定操作的Bot ID (SUPERUSER)"
            ),
            alias=["add", "开启"],
            help_text="设置/开启一个定时任务",
        ),
        Subcommand(
            "删除",
            Args["schedule_id?", int],
            Option("-p", Args["plugin_name", str], help_text="指定插件名"),
            Option("-g", Args["group_id", str], help_text="指定群组ID"),
            Option("-all", help_text="对所有群生效"),
            Option(
                "--bot", Args["bot_id", str], help_text="指定操作的Bot ID (SUPERUSER)"
            ),
            alias=["del", "rm", "remove", "关闭", "取消"],
            help_text="删除一个或多个定时任务",
        ),
        Subcommand(
            "暂停",
            Args["schedule_id?", int],
            Option("-all", help_text="对当前群所有任务生效"),
            Option("-p", Args["plugin_name", str], help_text="指定插件名"),
            Option("-g", Args["group_id", str], help_text="指定群组ID (SUPERUSER)"),
            Option(
                "--bot", Args["bot_id", str], help_text="指定操作的Bot ID (SUPERUSER)"
            ),
            alias=["pause"],
            help_text="暂停一个或多个定时任务",
        ),
        Subcommand(
            "恢复",
            Args["schedule_id?", int],
            Option("-all", help_text="对当前群所有任务生效"),
            Option("-p", Args["plugin_name", str], help_text="指定插件名"),
            Option("-g", Args["group_id", str], help_text="指定群组ID (SUPERUSER)"),
            Option(
                "--bot", Args["bot_id", str], help_text="指定操作的Bot ID (SUPERUSER)"
            ),
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
            Option("--cron", Args["cron_expr", str], help_text="设置 cron 表达式"),
            Option("--interval", Args["interval_expr", str], help_text="设置时间间隔"),
            Option("--date", Args["date_expr", str], help_text="设置特定执行日期"),
            Option(
                "--daily",
                Args["daily_expr", str],
                help_text="更新每天执行的时间 (如 08:20)",
            ),
            Option("--kwargs", Args["kwargs_str", str], help_text="更新参数"),
            alias=["update", "modify", "修改"],
            help_text="更新任务配置",
        ),
        Subcommand(
            "状态",
            Args["schedule_id", int],
            alias=["status", "info"],
            help_text="查看单个任务的详细状态",
        ),
        Subcommand(
            "插件列表",
            alias=["plugins"],
            help_text="列出所有可用的插件",
        ),
    ),
    priority=5,
    block=True,
    rule=admin_check(1),
)

schedule_cmd.shortcut(
    "任务状态",
    command="定时任务",
    arguments=["状态", "{%0}"],
    prefix=True,
)


class ScheduleTarget:
    pass


class TargetByID(ScheduleTarget):
    def __init__(self, id: int):
        self.id = id


class TargetByPlugin(ScheduleTarget):
    def __init__(
        self, plugin: str, group_id: str | None = None, all_groups: bool = False
    ):
        self.plugin = plugin
        self.group_id = group_id
        self.all_groups = all_groups


class TargetAll(ScheduleTarget):
    def __init__(self, for_group: str | None = None):
        self.for_group = for_group


TargetScope = TargetByID | TargetByPlugin | TargetAll | None


def create_target_parser(subcommand_name: str):
    async def dependency(
        event: Event,
        schedule_id: Match[int] = AlconnaMatch("schedule_id"),
        plugin_name: Match[str] = AlconnaMatch("plugin_name"),
        group_id: Match[str] = AlconnaMatch("group_id"),
        all_enabled: Query[bool] = Query(f"{subcommand_name}.all"),
    ) -> TargetScope:
        if schedule_id.available:
            return TargetByID(schedule_id.result)

        if plugin_name.available:
            p_name = plugin_name.result
            if all_enabled.available:
                return TargetByPlugin(plugin=p_name, all_groups=True)
            elif group_id.available:
                gid = group_id.result
                if gid.lower() == "all":
                    return TargetByPlugin(plugin=p_name, all_groups=True)
                return TargetByPlugin(plugin=p_name, group_id=gid)
            else:
                current_group_id = getattr(event, "group_id", None)
                return TargetByPlugin(
                    plugin=p_name,
                    group_id=str(current_group_id) if current_group_id else None,
                )

        if all_enabled.available:
            current_group_id = getattr(event, "group_id", None)
            if not current_group_id:
                await schedule_cmd.finish(
                    "私聊中单独使用 -all 选项时，必须使用 -g <群号> 指定目标。"
                )
            return TargetAll(for_group=str(current_group_id))

        return None

    return dependency


def parse_interval(interval_str: str) -> dict:
    match = re.match(r"(\d+)([smhd])", interval_str.lower())
    if not match:
        raise ValueError("时间间隔格式错误, 请使用如 '30m', '2h', '1d', '10s' 的格式。")
    value, unit = int(match.group(1)), match.group(2)
    if unit == "s":
        return {"seconds": value}
    if unit == "m":
        return {"minutes": value}
    if unit == "h":
        return {"hours": value}
    if unit == "d":
        return {"days": value}
    return {}


def parse_daily_time(time_str: str) -> dict:
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


async def GetBotId(bot: Bot, bot_id_match: Match[str] = AlconnaMatch("bot_id")) -> str:
    if bot_id_match.available:
        return bot_id_match.result
    return bot.self_id


def GetTargeter(subcommand: str):
    """
    依赖注入函数，用于解析命令参数并返回一个配置好的 ScheduleTargeter 实例。
    """

    async def dependency(
        event: Event,
        bot: Bot,
        schedule_id: Match[int] = AlconnaMatch("schedule_id"),
        plugin_name: Match[str] = AlconnaMatch("plugin_name"),
        group_id: Match[str] = AlconnaMatch("group_id"),
        all_enabled: Query[bool] = Query(f"{subcommand}.all"),
        bot_id_to_operate: str = Depends(GetBotId),
    ) -> ScheduleTargeter:
        if schedule_id.available:
            return scheduler_manager.target(id=schedule_id.result)

        if plugin_name.available:
            if all_enabled.available:
                return scheduler_manager.target(plugin_name=plugin_name.result)

            current_group_id = getattr(event, "group_id", None)
            gid = group_id.result if group_id.available else current_group_id
            return scheduler_manager.target(
                plugin_name=plugin_name.result,
                group_id=str(gid) if gid else None,
                bot_id=bot_id_to_operate,
            )

        if all_enabled.available:
            current_group_id = getattr(event, "group_id", None)
            gid = group_id.result if group_id.available else current_group_id
            is_su = await SUPERUSER(bot, event)
            if not gid and not is_su:
                await schedule_cmd.finish(
                    f"在私聊中对所有任务进行'{subcommand}'操作需要超级用户权限。"
                )

            if (gid and str(gid).lower() == "all") or (not gid and is_su):
                return scheduler_manager.target()

            return scheduler_manager.target(
                group_id=str(gid) if gid else None, bot_id=bot_id_to_operate
            )

        await schedule_cmd.finish(
            f"'{subcommand}'操作失败：请提供任务ID，"
            f"或通过 -p <插件名> 或 -all 指定要操作的任务。"
        )

    return Depends(dependency)

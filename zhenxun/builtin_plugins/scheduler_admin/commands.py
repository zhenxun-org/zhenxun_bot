from arclet.alconna import ArparmaBehavior
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Arparma,
    Field,
    MultiVar,
    Option,
    Subcommand,
    on_alconna,
    store_true,
)

from zhenxun.utils.rules import admin_check


def create_time_options() -> list[Option]:
    """创建一组用于定义任务执行时间的通用选项"""
    return [
        Option("--cron", Args["cron_expr", str], help_text="设置 cron 表达式"),
        Option("--interval", Args["interval_expr", str], help_text="设置时间间隔"),
        Option("--date", Args["date_expr", str], help_text="设置特定执行日期"),
        Option(
            "--daily",
            Args["daily_expr", str],
            help_text="设置每天执行的时间 (如 08:20)",
        ),
    ]


def create_targeting_options() -> list[Option]:
    """创建一组用于定位定时任务的通用选项"""
    return [
        Option("-p", Args["plugin_name", str], help_text="按插件名筛选"),
        Option("-u", Args["user_id", str], help_text="指定用户ID"),
        Option(
            "-g",
            Args["group_ids", MultiVar(str)],
            help_text="指定一个或多个群组ID (SUPERUSER)",
        ),
        Option("-t", Args["tag_name", str], help_text="指定标签"),
        Option("--all", action=store_true, help_text="对所有群生效"),
        Option("--global", action=store_true, help_text="操作全局任务"),
        Option("--bot", Args["bot_id", str], help_text="指定操作的Bot ID (SUPERUSER)"),
    ]


class SchedulerAdminBehavior(ArparmaBehavior):
    """对定时任务命令的参数进行复杂的复合验证。"""

    def _validate_time_options(self, interface: Arparma, subcommand: str):
        """验证时间选项 (--cron, --interval, --date, --daily) 的互斥性。"""
        time_options = ["cron", "interval", "date", "daily"]
        provided_options = [
            f"--{opt}" for opt in time_options if interface.query(f"{subcommand}.{opt}")
        ]
        if len(provided_options) > 1:
            interface.behave_fail(
                f"时间选项 {', '.join(provided_options)} 不能同时使用，请只选择一个。"
            )

    def _validate_target_options(self, interface: Arparma, subcommand: str):
        """验证目标选项 (-u, -g, -t, --all, --global) 的互斥性。"""
        target_flags = {
            "-u": "u",
            "-g": "g",
            "-t": "t",
            "--all": "all",
            "--global": "global",
        }
        provided_flags = [
            flag
            for flag, name in target_flags.items()
            if interface.query(f"{subcommand}.{name}")
        ]

        if len(provided_flags) > 1:
            interface.behave_fail(
                f"目标选项 {', '.join(provided_flags)} 是互斥的，请只选择一个。"
            )

    def operate(self, interface: Arparma):
        subcommand = next(iter(interface.subcommands.keys()), None)
        if not subcommand:
            return

        if subcommand in {"设置", "更新"}:
            self._validate_time_options(interface, subcommand)
        if subcommand in {"查看", "设置", "删除", "暂停", "恢复"}:
            self._validate_target_options(interface, subcommand)


schedule_cmd = on_alconna(
    Alconna(
        "定时任务",
        Subcommand(
            "查看",
            *create_targeting_options(),
            Option("--page", Args["page", int, 1], help_text="指定页码"),
            alias=["ls", "list"],
            help_text="查看定时任务",
        ),
        Subcommand(
            "设置",
            Args["plugin_name", str],
            *create_time_options(),
            Option(
                "-g", Args["group_ids", MultiVar(str)], help_text="指定一个或多个群组ID"
            ),
            Option("-u", Args["user_id", str], help_text="指定用户ID"),
            Option("-t", Args["tag_name", str], help_text="指定一个群组标签"),
            Option("--all", action=store_true, help_text="对所有群生效"),
            Option("--global", action=store_true, help_text="设置为全局任务"),
            Option("--name", Args["job_name", str], help_text="为任务设置一个别名"),
            Option("--kwargs", Args["kwargs_str", str], help_text="设置任务参数"),
            Option(
                "--params-cli",
                Args["cli_string", str],
                help_text="传递给插件任务的原始命令行参数字符串",
            ),
            Option(
                "--jitter",
                Args["jitter_seconds", int],
                help_text="设置触发时间抖动(秒)",
            ),
            Option(
                "--spread",
                Args["spread_seconds", int],
                help_text="设置多目标执行的分散延迟(秒)",
            ),
            Option(
                "--fixed-interval",
                Args["interval_seconds", int],
                help_text="设置任务间的固定执行间隔(秒)，将强制串行",
            ),
            Option(
                "--permission",
                Args["perm_level", int],
                help_text="设置任务的管理权限等级",
            ),
            Option(
                "--bot", Args["bot_id", str], help_text="指定操作的Bot ID (SUPERUSER)"
            ),
            alias=["add", "开启"],
            help_text="设置/开启一个定时任务",
        ),
        Subcommand(
            "删除",
            Args[
                "schedule_ids?",
                MultiVar(int),
                Field(unmatch_tips=lambda text: f"任务ID '{text}' 必须是数字！"),
            ],
            *create_targeting_options(),
            alias=["del", "rm", "remove", "关闭", "取消"],
            help_text="删除一个或多个定时任务",
        ),
        Subcommand(
            "暂停",
            Args[
                "schedule_ids?",
                MultiVar(int),
                Field(unmatch_tips=lambda text: f"任务ID '{text}' 必须是数字！"),
            ],
            *create_targeting_options(),
            alias=["pause"],
            help_text="暂停一个或多个定时任务",
        ),
        Subcommand(
            "恢复",
            Args[
                "schedule_ids?",
                MultiVar(int),
                Field(unmatch_tips=lambda text: f"任务ID '{text}' 必须是数字！"),
            ],
            *create_targeting_options(),
            alias=["resume"],
            help_text="恢复一个或多个定时任务",
        ),
        Subcommand(
            "执行",
            Args[
                "schedule_id",
                int,
                Field(
                    missing_tips=lambda: "请提供要立即执行的任务ID！",
                    unmatch_tips=lambda text: f"任务ID '{text}' 必须是数字！",
                ),
            ],
            alias=["trigger", "run"],
            help_text="立即执行一次任务",
        ),
        Subcommand(
            "更新",
            Args[
                "schedule_id",
                int,
                Field(
                    missing_tips=lambda: "请提供要更新的任务ID！",
                    unmatch_tips=lambda text: f"任务ID '{text}' 必须是数字！",
                ),
            ],
            *create_time_options(),
            Option("--kwargs", Args["kwargs_str", str], help_text="更新参数"),
            alias=["update", "modify", "修改"],
            help_text="更新任务配置",
        ),
        Subcommand(
            "状态",
            Args[
                "schedule_id",
                int,
                Field(
                    missing_tips=lambda: "请提供要查看状态的任务ID！",
                    unmatch_tips=lambda text: f"任务ID '{text}' 必须是数字！",
                ),
            ],
            alias=["status", "info"],
            help_text="查看单个任务的详细状态",
        ),
        Subcommand(
            "插件列表",
            alias=["plugins"],
            help_text="列出所有可用的插件",
        ),
        behaviors=[SchedulerAdminBehavior()],
    ),
    priority=5,
    block=True,
    skip_for_unmatch=False,
    aliases={"schedule", "cron", "job"},
    rule=admin_check("SchedulerManager", "SCHEDULE_ADMIN_LEVEL"),
)


schedule_cmd.shortcut(
    "任务状态",
    command="定时任务",
    arguments=["状态", "{%0}"],
    prefix=True,
)

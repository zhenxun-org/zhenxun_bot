from nonebot.rule import to_me
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    MultiVar,
    Option,
    Subcommand,
    on_alconna,
    store_true,
)

from zhenxun.utils.rules import admin_check

_status_matcher = on_alconna(
    Alconna(
        "switch",
        Option("--task", action=store_true, help_text="被动技能"),
        Option("-df|--default", action=store_true, help_text="进群默认开关"),
        Option("--all-plugins", action=store_true, help_text="所有插件/功能"),
        Option("--all", action=store_true, help_text="所有群组 (超级用户专用)"),
        Option("-g|--group", Args["groups", MultiVar(str)], help_text="指定群组"),
        Option("-t|--tag", Args["tag", str], help_text="指定标签"),
        Option("-o|--only", action=store_true, help_text="白名单模式(仅在目标群开启)"),
        Option("-s|--su", action=store_true, help_text="操作超级用户专用字段"),
        Subcommand(
            "check",
            Args["plugin_name", [str, int]],
        ),
        Subcommand(
            "open",
            Args["plugin_names?", MultiVar(str)],
            Option(
                "--type",
                Args["block_type?", ["all", "a", "private", "p", "group", "g"]],
                help_text="全局禁用范围",
            ),
        ),
        Subcommand(
            "close",
            Args["plugin_names?", MultiVar(str)],
            Option(
                "--type",
                Args["block_type?", ["all", "a", "private", "p", "group", "g"]],
                help_text="全局禁用范围",
            ),
        ),
    ),
    rule=admin_check("plugin_switch", "CHANGE_GROUP_SWITCH_LEVEL"),
    priority=5,
    block=True,
)

_group_status_matcher = on_alconna(
    Alconna(
        "group-status",
        Args["status", ["sleep", "wake", "check"]],
        Option("-g|--group", Args["groups", MultiVar(str)], help_text="指定群组"),
        Option("-t|--tag", Args["tag", str], help_text="指定标签"),
        Option("--all", action=store_true, help_text="所有群组"),
        Option("-o|--only", action=store_true, help_text="白名单模式(仅在目标群醒来)"),
    ),
    rule=admin_check("plugin_switch", "CHANGE_GROUP_SWITCH_LEVEL") & to_me(),
    priority=5,
    block=True,
)

_status_matcher.shortcut(
    r"插件列表",
    command="switch",
    arguments=[],
    prefix=True,
)

_status_matcher.shortcut(
    r"查看(功能|插件)?状态",
    command="switch check {*}",
    prefix=True,
)

_status_matcher.shortcut(
    r"查看(群)?被动状态",
    command="switch check {*} --task",
    prefix=True,
)

_status_matcher.shortcut(
    r"(群)?被动状态",
    command="switch",
    arguments=["--task"],
    prefix=True,
)


def _switch_wrapper(slot: str, content: str | None, context: dict) -> str:
    """动态映射转换函数"""
    if slot == "action":
        return "open" if content == "开启" else "close"
    if slot == "all" and content:
        return "--all-plugins"
    if slot == "default" and content:
        return "-df"
    if slot == "type" and content:
        return "--task" if "被动" in content else ""
    return ""


_status_matcher.shortcut(
    r"^(?P<action>开启|关闭)\s*(?P<all>所有|全部)?\s*(?P<default>默认)?\s*(?P<type>群被动|被动|插件|功能)?\s*",
    command="switch {all} {default} {type} {action} {* }",
    wrapper=_switch_wrapper,  # type: ignore
    prefix=True,
)


_group_status_matcher.shortcut(
    r"醒来",
    command="group-status",
    arguments=["wake"],
    prefix=True,
)

_group_status_matcher.shortcut(
    r"休息(吧)?",
    command="group-status",
    arguments=["sleep"],
    prefix=True,
)

_group_status_matcher.shortcut(
    r"查看群(状态|信息)",
    command="group-status",
    arguments=["check"],
    prefix=True,
)

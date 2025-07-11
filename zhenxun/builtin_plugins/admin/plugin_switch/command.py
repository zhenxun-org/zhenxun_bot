from nonebot.rule import to_me
from nonebot_plugin_alconna import (
    Alconna,
    Args,
    Option,
    Subcommand,
    on_alconna,
    store_true,
)

from zhenxun.utils.rules import admin_check, ensure_group

_status_matcher = on_alconna(
    Alconna(
        "switch",
        Option("-t|--task", action=store_true, help_text="被动技能"),
        Option("-df|--default", action=store_true, help_text="进群默认开关"),
        Option("--all", action=store_true, help_text="全部插件/被动"),
        Option("-g|--group", Args["group?", str], help_text="指定群组"),
        Subcommand(
            "open",
            Args["plugin_name?", [str, int]],
        ),
        Subcommand(
            "close",
            Args["plugin_name?", [str, int]],
            Option(
                "-t|--type",
                Args["block_type?", ["all", "a", "private", "p", "group", "g"]],
            ),
        ),
    ),
    rule=admin_check("plugin_switch", "CHANGE_GROUP_SWITCH_LEVEL"),
    priority=5,
    block=True,
)

_group_status_matcher = on_alconna(
    Alconna("group-status", Args["status", ["sleep", "wake"]]),
    rule=admin_check("plugin_switch", "CHANGE_GROUP_SWITCH_LEVEL")
    & ensure_group
    & to_me(),
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
    r"群被动状态",
    command="switch",
    arguments=["--task"],
    prefix=True,
)

_status_matcher.shortcut(
    r"开启(所有|全部)默认群被动",
    command="switch",
    arguments=["open", "--task", "--all", "-df"],
    prefix=True,
)

_status_matcher.shortcut(
    r"关闭(所有|全部)默认群被动",
    command="switch",
    arguments=["close", "--task", "--all", "-df"],
    prefix=True,
)

_status_matcher.shortcut(
    r"开启群被动\s*(?P<name>.+)",
    command="switch",
    arguments=["open", "{name}", "--task"],
    prefix=True,
)

_status_matcher.shortcut(
    r"关闭群被动\s*(?P<name>.+)",
    command="switch",
    arguments=["close", "{name}", "--task"],
    prefix=True,
)

_status_matcher.shortcut(
    r"开启默认群被动\s*(?P<name>.+)",
    command="switch",
    arguments=["open", "{name}", "--task", "-df"],
    prefix=True,
)

_status_matcher.shortcut(
    r"关闭默认群被动\s*(?P<name>.+)",
    command="switch",
    arguments=["close", "{name}", "--task", "-df"],
    prefix=True,
)


_status_matcher.shortcut(
    r"开启(所有|全部)群被动",
    command="switch",
    arguments=["open", "--task", "--all"],
    prefix=True,
)

_status_matcher.shortcut(
    r"关闭(所有|全部)群被动",
    command="switch",
    arguments=["close", "--task", "--all"],
    prefix=True,
)


_status_matcher.shortcut(
    r"开启所有(插件|功能)",
    command="switch",
    arguments=["open", "s", "--all"],
    prefix=True,
)

_status_matcher.shortcut(
    r"开启所有(插件|功能)df",
    command="switch",
    arguments=["open", "s", "-df", "--all"],
    prefix=True,
)

_status_matcher.shortcut(
    r"开启(插件|功能)df(?P<name>.+)",
    command="switch",
    arguments=["open", "{name}", "-df"],
    prefix=True,
)

_status_matcher.shortcut(
    r"开启(?P<name>.+)",
    command="switch",
    arguments=["open", "{name}"],
    prefix=True,
)


_status_matcher.shortcut(
    r"关闭所有(插件|功能)",
    command="switch",
    arguments=["close", "s", "--all"],
    prefix=True,
)

_status_matcher.shortcut(
    r"关闭所有(插件|功能)df",
    command="switch",
    arguments=["close", "s", "-df", "--all"],
    prefix=True,
)

_status_matcher.shortcut(
    r"关闭(插件|功能)df(?P<name>.+)",
    command="switch",
    arguments=["close", "{name}", "-df"],
    prefix=True,
)

_status_matcher.shortcut(
    r"关闭(?P<name>.+)",
    command="switch",
    arguments=["close", "{name}"],
    prefix=True,
)


_group_status_matcher.shortcut(
    r"醒来",
    command="group-status",
    arguments=["wake"],
    prefix=True,
)

_group_status_matcher.shortcut(
    r"休息吧",
    command="group-status",
    arguments=["sleep"],
    prefix=True,
)

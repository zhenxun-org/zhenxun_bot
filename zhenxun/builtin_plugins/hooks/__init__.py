from pathlib import Path

import nonebot

from zhenxun.configs.config import Config

Config.add_plugin_config(
    "hook",
    "CHECK_NOTICE_INFO_CD",
    300,
    help="群检测，个人权限检测等各种检测提示信息cd",
    default_value=300,
    type=int,
)

Config.add_plugin_config(
    "hook",
    "MALICIOUS_BAN_TIME",
    30,
    help="恶意命令触发检测触发后ban的时长（分钟）",
    default_value=30,
    type=int,
)

Config.add_plugin_config(
    "hook",
    "MALICIOUS_CHECK_TIME",
    5,
    help="恶意命令触发检测规定时间内（秒）",
    default_value=5,
    type=int,
)

Config.add_plugin_config(
    "hook",
    "MALICIOUS_BAN_COUNT",
    6,
    help="恶意命令触发检测最大触发次数",
    default_value=6,
    type=int,
)

Config.add_plugin_config(
    "hook",
    "MALICIOUS_CHECK_MODE",
    "off",
    help="恶意触发检测模式：off=关闭，blacklist=仅列表插件检测，whitelist=列表插件跳过检测",
    default_value="off",
    type=str,
)

Config.add_plugin_config(
    "hook",
    "MALICIOUS_CHECK_PLUGINS",
    [],
    help="恶意触发检测插件列表，按模式作为黑名单或白名单使用，填插件模块名",
    default_value=[],
    type=list,
)

Config.add_plugin_config(
    "hook",
    "IS_SEND_TIP_MESSAGE",
    True,
    help="是否发送阻断时提示消息",
    default_value=True,
    type=bool,
)

nonebot.load_plugins(str(Path(__file__).parent.resolve()))

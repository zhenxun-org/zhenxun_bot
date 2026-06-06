"""QQ official platform observer.

Official QQ identifiers are not in the same namespace as OneBot QQ numbers.
This observer intentionally avoids writing legacy identity tables; runtime auth
uses a non-persistent group snapshot when needed.
"""

from nonebot import on_message
from nonebot_plugin_uninfo import Uninfo

from zhenxun.utils.platform import PlatformUtils


def rule(session: Uninfo) -> bool:
    return PlatformUtils.is_qbot(session)


_matcher = on_message(priority=999, block=False, rule=rule)


@_matcher.handle()
async def _():
    return

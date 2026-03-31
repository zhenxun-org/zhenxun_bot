import re
import time

from zhenxun.models.group_console import GroupConsole
from zhenxun.models.plugin_info import PluginInfo
from zhenxun.services.cache.runtime_cache import GroupSnapshot
from zhenxun.services.log import logger

from .config import LOGGER_COMMAND, WARNING_THRESHOLD, SwitchEnum
from .exception import SkipPluginException

_GROUP_WAKE_PATTERN = re.compile(r"^醒来$", re.IGNORECASE)
_GROUP_WAKE_CANONICAL_PATTERN = re.compile(r"^group-status\s+wake$", re.IGNORECASE)


def _is_group_wake_command(plugin: PluginInfo, text: str) -> bool:
    if "plugin_switch" not in (plugin.module or ""):
        return False
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return False
    if (
        _GROUP_WAKE_PATTERN.match(normalized) is not None
        or _GROUP_WAKE_CANONICAL_PATTERN.match(normalized) is not None
    ):
        return True
    # 兼容 to_me 前缀场景：如“真寻 醒来”
    tokens = normalized.split(" ")
    return len(tokens) == 2 and tokens[-1] == SwitchEnum.ENABLE


async def auth_group(
    plugin: PluginInfo,
    group: GroupConsole | GroupSnapshot | None,
    text: str | None,
    group_id: str | None,
):
    """群黑名单检测 群总开关检测

    参数:
        plugin: PluginInfo
        group: GroupConsole
        message: UniMsg
    """
    if not group_id:
        return

    start_time = time.time()

    try:
        text = text or ""

        if not group:
            raise SkipPluginException("群组信息不存在...")
        if group.level < 0:
            raise SkipPluginException("群组黑名单, 目标群组群权限权限-1...")
        if not _is_group_wake_command(plugin, text) and not group.status:
            raise SkipPluginException("群组休眠状态...")
        if plugin.level > group.level:
            raise SkipPluginException(
                f"{plugin.name}({plugin.module}) 群等级限制，"
                f"该功能需要的群等级: {plugin.level}..."
            )
    finally:
        # 记录执行时间
        elapsed = time.time() - start_time
        if elapsed > WARNING_THRESHOLD:  # 记录耗时超过500ms的检查
            logger.warning(
                f"auth_group 耗时: {elapsed:.3f}s, plugin={plugin.module}",
                LOGGER_COMMAND,
                group_id=group_id,
            )
